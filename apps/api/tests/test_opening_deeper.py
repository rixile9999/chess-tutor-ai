"""더 깊이: GET /openings/lines and GET /openings/masters (M8d-4, plan §10.4).

The engine is stubbed at `services.analysis.get_lines` — these tests are about the shape of
the answer, not about Stockfish's opinion — and the master explorer is stubbed with respx, the
way tests/test_opening_guide.py stubs it. Without a token the explorer is never called.
"""

from __future__ import annotations

from collections.abc import Iterator

import chess
import httpx
import pytest
import respx
from httpx import AsyncClient

from chess_tutor.config import get_settings
from chess_tutor.engine import EngineBusy
from chess_tutor.schemas import EngineLine, Score
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import opening_deeper
from chess_tutor.services import openings_map as om

RUY_LOPEZ = "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3"
"""After 1.e4 e5 2.Nf3 Nc6 3.Bb5 — Black to move, the position the mockup's panel is about."""
ILLEGAL_FEN = "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1"
"""White to move with Black already in check: no game reaches it."""

PV = ["a6", "Ba4", "Nf6", "O-O", "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O", "h3"]
"""Twelve plies, longer than PV_CAP, so the cut is visible."""


@pytest.fixture(autouse=True)
def _no_explorer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No token and no leftovers from another test's master cache."""
    monkeypatch.setattr(get_settings(), "lichess_token", None)
    om._master_cache.clear()
    yield
    om._master_cache.clear()


def engine_line(rank: int, cp: int, sans: list[str]) -> EngineLine:
    """A cached engine line for the Ruy Lopez position; the UCI only has to line up by index."""
    board = chess.Board(RUY_LOPEZ)
    ucis = []
    for san in sans:
        move = board.push_san(san)
        ucis.append(move.uci())
    return EngineLine(rank=rank, score=Score(cp=cp), pv=list(sans), pv_uci=ucis)


def patch_engine(monkeypatch: pytest.MonkeyPatch, lines: list[EngineLine]) -> list[tuple[str, int]]:
    """Stand in for the engine; returns the (fen, depth) the service asked about."""
    calls: list[tuple[str, int]] = []

    async def get_lines(
        fen: str, depth: int | None = None, multipv: int | None = None
    ) -> list[EngineLine]:
        calls.append((fen, depth or 0))
        return list(lines)

    monkeypatch.setattr(analysis_svc, "get_lines", get_lines)
    return calls


def patch_engine_error(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    async def get_lines(*_args: object, **_kwargs: object) -> list[EngineLine]:
        raise exc

    monkeypatch.setattr(analysis_svc, "get_lines", get_lines)


def _explorer(*moves: dict[str, object]) -> respx.Route:
    return respx.get(om.EXPLORER_URL).mock(
        return_value=httpx.Response(200, json={"moves": list(moves)})
    )


# ---------- engine lines ----------


async def test_lines_returns_the_move_score_and_pv_of_every_line(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = patch_engine(monkeypatch, [engine_line(1, 31, PV)])
    res = await aclient.get("/openings/lines", params={"fen": RUY_LOPEZ, "depth": 12})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["fen"] == RUY_LOPEZ and body["depth"] == 12
    assert calls == [(RUY_LOPEZ, 12)]

    line = body["lines"][0]
    assert line["san"] == "a6" and line["uci"] == "a7a6"
    assert line["score"] == {"cp": 31, "mate": None}
    # The first move is part of the line, and the tail is cut at PV_CAP.
    assert line["pv_san"] == PV[: opening_deeper.PV_CAP]


async def test_lines_are_ordered_by_engine_rank_and_capped_at_multipv(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_engine(
        monkeypatch,
        [
            engine_line(3, 10, ["Nf6", "O-O"]),
            engine_line(1, 31, ["a6", "Ba4"]),
            engine_line(4, 5, ["Nge7"]),
            engine_line(2, 22, ["d6", "c3"]),
            engine_line(5, -20, ["f5"]),
        ],
    )
    res = await aclient.get("/openings/lines", params={"fen": RUY_LOPEZ})
    assert res.status_code == 200, res.text
    # Default multipv is 3: five cached lines, three shown, best first.
    assert [line["san"] for line in res.json()["lines"]] == ["a6", "d6", "Nf6"]


async def test_lines_skip_a_position_with_no_moves(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mated position still has a score but no PV; a line without moves is not shown."""
    patch_engine(monkeypatch, [EngineLine(rank=1, score=Score(mate=0), pv=[], pv_uci=[])])
    res = await aclient.get("/openings/lines", params={"fen": RUY_LOPEZ})
    assert res.status_code == 200 and res.json()["lines"] == []


async def test_lines_reject_a_bad_fen(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/lines", params={"fen": "definitely not a fen"})
    assert res.status_code == 422 and "FEN" in res.json()["detail"]
    res = await aclient.get("/openings/lines", params={"fen": ILLEGAL_FEN})
    assert res.status_code == 422 and "체스 규칙" in res.json()["detail"]


async def test_lines_answer_503_when_the_engine_is_unavailable(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_engine_error(monkeypatch, RuntimeError("Stockfish not found"))
    res = await aclient.get("/openings/lines", params={"fen": RUY_LOPEZ})
    assert res.status_code == 503 and "엔진" in res.json()["detail"]

    patch_engine_error(monkeypatch, EngineBusy())
    res = await aclient.get("/openings/lines", params={"fen": RUY_LOPEZ})
    assert res.status_code == 503 and "사용 중" in res.json()["detail"]


# ---------- master statistics ----------


def test_percentages_always_add_up_to_a_hundred() -> None:
    assert opening_deeper.percentages(50, 30, 20) == (50, 30, 20)
    assert sum(opening_deeper.percentages(1, 1, 1)) == 100
    assert sum(opening_deeper.percentages(333, 333, 334)) == 100
    assert opening_deeper.percentages(0, 0, 0) == (0, 0, 0)


@respx.mock
async def test_masters_returns_percentages_and_the_raw_game_count(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "lichess_token", "tok")
    route = _explorer(
        {
            "uci": "a7a6",
            "san": "a6",
            "white": 500,
            "draws": 300,
            "black": 200,
            "averageRating": 2420,
        },
        {"uci": "g8f6", "san": "Nf6", "white": 90, "draws": 60, "black": 50},
        {"uci": "a1a8", "san": "??", "white": 1, "draws": 0, "black": 0},
    )
    res = await aclient.get("/openings/masters", params={"fen": RUY_LOPEZ})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["available"] is True and body["reason"] is None
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"

    # Busiest first, and the illegal row the explorer would never send is dropped.
    assert [m["san"] for m in body["moves"]] == ["a6", "Nf6"]
    a6 = body["moves"][0]
    assert a6["uci"] == "a7a6" and a6["games"] == 1000
    assert (a6["white"], a6["draws"], a6["black"]) == (50, 30, 20)
    assert a6["avg_rating"] == 2420
    assert body["moves"][1]["avg_rating"] is None


async def test_masters_say_so_without_a_token(aclient: AsyncClient) -> None:
    """No token, no network call: respx would raise on one because it is not mocked here."""
    res = await aclient.get("/openings/masters", params={"fen": RUY_LOPEZ})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["available"] is False and body["moves"] == []
    assert body["reason"] == opening_deeper.NO_TOKEN


@respx.mock
async def test_masters_report_an_unreachable_explorer_without_failing(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "lichess_token", "tok")
    respx.get(om.EXPLORER_URL).mock(return_value=httpx.Response(500))
    res = await aclient.get("/openings/masters", params={"fen": RUY_LOPEZ})
    assert res.status_code == 200, res.text
    assert res.json() == {
        "fen": RUY_LOPEZ,
        "available": False,
        "reason": opening_deeper.EXPLORER_DOWN,
        "moves": [],
    }


@respx.mock
async def test_masters_distinguish_no_games_from_a_failed_call(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer with no moves is a position masters never reached, not a connection problem."""
    monkeypatch.setattr(get_settings(), "lichess_token", "tok")
    _explorer()
    res = await aclient.get("/openings/masters", params={"fen": RUY_LOPEZ})
    assert res.status_code == 200, res.text
    assert res.json()["available"] is True and res.json()["moves"] == []
    assert res.json()["reason"] is None


async def test_masters_reject_a_bad_fen(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/masters", params={"fen": ILLEGAL_FEN})
    assert res.status_code == 422 and "체스 규칙" in res.json()["detail"]
