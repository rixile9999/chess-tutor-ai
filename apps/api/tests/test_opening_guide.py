"""Position guide: book candidates, the master overlay and GET /openings/position.

The book is the bundled lichess TSV, so the expectations name real openings. Only the master
overlay talks to the network and respx stubs it; without a token it never runs at all.
"""

from __future__ import annotations

from collections.abc import Iterator

import chess
import httpx
import pytest
import respx
from httpx import AsyncClient

from chess_tutor.config import get_settings
from chess_tutor.services import opening_guide
from chess_tutor.services import openings_map as om

RUY_LOPEZ = "e4 e5 Nf3 Nc6 Bb5"
NAJDORF = "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6"
OKELLY = "e4 c5 Nf3 a6 d4 cxd4 Nxd4 Nf6 Nc3 d6"
"""Two move orders that reach the same Najdorf position (tests/test_openings_map.py)."""

ILLEGAL_FEN = "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1"
"""White to move with Black already in check: no game reaches it."""


def board(moves: str = "") -> chess.Board:
    b = chess.Board()
    for san in moves.split():
        b.push_san(san)
    return b


def fen(moves: str = "") -> str:
    return board(moves).fen()


@pytest.fixture(autouse=True)
def _no_explorer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No master overlay and no leftovers from another test's cache."""
    monkeypatch.setattr(get_settings(), "lichess_token", None)
    om._master_cache.clear()
    opening_guide.clear_cache()
    yield
    om._master_cache.clear()
    opening_guide.clear_cache()


# ---------- candidates ----------


def test_the_start_position_offers_the_main_first_moves() -> None:
    cards = opening_guide.candidates(chess.Board())
    by_san = {c.san: c for c in cards}
    assert {"e4", "d4", "c4", "Nf3"} <= set(by_san)
    e4 = by_san["e4"]
    assert e4.label == "1.e4" and e4.uci == "e2e4"
    assert e4.name == "King's Pawn Game" and e4.eco == "B00"
    assert e4.named_here and e4.to_name == []
    assert chess.Board(e4.fen_after).piece_at(chess.E4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert e4.master_games is None and not e4.master_only


def test_black_labels_carry_the_move_number_and_ellipsis() -> None:
    cards = opening_guide.candidates(board(RUY_LOPEZ))
    a6 = next(c for c in cards if c.san == "a6")
    assert a6.label == "3…a6"
    assert a6.named_here and a6.name == "Ruy Lopez: Morphy Defense" and a6.eco == "C70"
    assert a6.to_name == []


def test_an_unnamed_arrival_keeps_the_moves_up_to_the_next_name() -> None:
    """3.d3 is in the book but the position it reaches is not; the line is forced from there."""
    cards = opening_guide.candidates(board("e4 e5 Nf3 Nc6"))
    d3 = next(c for c in cards if c.san == "d3")
    assert not d3.named_here
    assert d3.to_name == ["f5", "exf5"]
    assert d3.name == "Latvian Gambit: Clam Gambit"
    # Named arrivals come first, so the one unnamed card is last.
    assert cards[-1].san == "d3"


def test_a_transposition_sees_the_same_candidates() -> None:
    najdorf = opening_guide.guide(fen(NAJDORF))
    okelly = opening_guide.guide(fen(OKELLY))
    assert najdorf.name == okelly.name == "Sicilian Defense: Najdorf Variation"
    assert [c.san for c in najdorf.candidates] == [c.san for c in okelly.candidates]
    assert [c.label for c in najdorf.candidates] == [c.label for c in okelly.candidates]


# ---------- guide ----------


def test_guide_carries_the_name_the_structure_and_every_setup() -> None:
    guide = opening_guide.guide(fen(RUY_LOPEZ), color="white")
    assert guide.side == "black" and guide.in_book
    assert guide.name == "Ruy Lopez" and guide.eco == "C60"
    assert guide.structure.key and guide.structure.name
    assert len(guide.setups) == 14
    assert guide.setups[0].side == "black"  # the side to move comes first
    assert all(s.plans for s in guide.setups)
    assert guide.fen == fen(RUY_LOPEZ)


def test_guide_off_book_has_no_name_but_still_answers() -> None:
    guide = opening_guide.guide(fen("e4 e5 Nf3 Nc6 Bb5 h6 Ba4"))
    assert not guide.in_book and guide.name is None and guide.eco is None
    assert len(guide.setups) == 14


def test_guide_refuses_a_position_that_breaks_the_rules() -> None:
    with pytest.raises(ValueError, match="체스 규칙"):
        opening_guide.guide(ILLEGAL_FEN)
    with pytest.raises(ValueError, match="FEN을 읽을 수 없습니다"):
        opening_guide.guide("not a fen")


# ---------- master overlay ----------


def _explorer(*moves: dict[str, object]) -> respx.Route:
    return respx.get(om.EXPLORER_URL).mock(
        return_value=httpx.Response(200, json={"moves": list(moves)})
    )


@respx.mock
def test_master_statistics_are_laid_over_the_book(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "lichess_token", "tok")
    route = _explorer(
        {"uci": "a7a6", "san": "a6", "white": 500, "draws": 300, "black": 200},
        {"uci": "h7h6", "san": "h6", "white": 10, "draws": 10, "black": 30},
    )
    cards = opening_guide.candidates(board(RUY_LOPEZ), color="black", masters=True)
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"

    a6 = next(c for c in cards if c.san == "a6")
    assert a6.master_games == 1000
    assert a6.master_score == pytest.approx(0.35)  # black: (200 + 150) / 1000
    assert not a6.master_only and a6.named_here

    # A move only masters play joins the list without a book name.
    h6 = next(c for c in cards if c.san == "h6")
    assert h6.master_only and h6.name == "" and h6.eco == ""
    assert not h6.named_here and h6.master_games == 50
    assert h6.label == "3…h6"
    assert cards[-1] is h6  # unnamed arrivals sort last

    # The same request as White flips the score, and the explorer is only called once.
    calls = route.call_count
    white = opening_guide.candidates(board(RUY_LOPEZ), color="white", masters=True)
    assert next(c for c in white if c.san == "a6").master_score == pytest.approx(0.65)
    assert route.call_count == calls  # openings_map caches per process


@respx.mock
def test_master_moves_sort_the_named_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "lichess_token", "tok")
    _explorer(
        {"uci": "g8f6", "san": "Nf6", "white": 90, "draws": 60, "black": 50},
        {"uci": "a7a6", "san": "a6", "white": 500, "draws": 300, "black": 200},
    )
    cards = opening_guide.candidates(board(RUY_LOPEZ), color="white", masters=True)
    assert [c.san for c in cards[:2]] == ["a6", "Nf6"]
    assert all(c.master_games is None for c in cards[2:])


@respx.mock
def test_an_unreachable_explorer_still_returns_the_book(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "lichess_token", "tok")
    respx.get(om.EXPLORER_URL).mock(return_value=httpx.Response(500))
    cards = opening_guide.candidates(board(RUY_LOPEZ), color="black", masters=True)
    assert len(cards) == 18 and all(c.master_games is None for c in cards)
    # A failed call is not remembered as "no master games": asking again tries once more.
    route = _explorer({"uci": "a7a6", "san": "a6", "white": 4, "draws": 2, "black": 4})
    again = opening_guide.candidates(board(RUY_LOPEZ), color="black", masters=True)
    assert route.called
    assert next(c for c in again if c.san == "a6").master_games == 10


def test_masters_are_skipped_without_a_token() -> None:
    """No token, no network call: respx would raise on one because it is not mocked here."""
    cards = opening_guide.candidates(board(RUY_LOPEZ), masters=True)
    assert cards and all(c.master_games is None for c in cards)


# ---------- endpoint ----------


async def test_position_endpoint(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/position", params={"fen": fen(RUY_LOPEZ)})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "Ruy Lopez" and body["side"] == "black"
    assert len(body["setups"]) == 14
    a6 = next(c for c in body["candidates"] if c["san"] == "a6")
    assert a6["label"] == "3…a6" and a6["named_here"] is True
    assert a6["master_games"] is None


async def test_position_endpoint_rejects_a_bad_fen(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/position", params={"fen": ILLEGAL_FEN})
    assert res.status_code == 422
    assert "체스 규칙" in res.json()["detail"]
    res = await aclient.get("/openings/position", params={"fen": "definitely not a fen"})
    assert res.status_code == 422


async def test_position_endpoint_rejects_a_bad_colour(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/position", params={"fen": fen(), "color": "purple"})
    assert res.status_code == 422
