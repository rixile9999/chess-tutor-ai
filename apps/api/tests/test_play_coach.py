"""The live-position coach: three hint levels and the after-move check.

Hint levels 1 and 2 never touch the engine, so they run everywhere; anything that needs a
search is skipped without a Stockfish binary and asks for a small depth.
"""

from collections.abc import Iterable, Iterator

import chess
import pytest
from fastapi.testclient import TestClient

from chess_tutor.engine import find_stockfish
from chess_tutor.services import analysis
from chess_tutor.services import maia as maia_service
from chess_tutor.verify import Claim, verify_all

DEPTH = 8
"""Small on purpose: these tests are about the shape of the answer, not the engine's opinion."""

MOCKUP_FEN = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
"""Mockup review position: Black to move, ...exd5 wins a piece."""
SICILIAN_FEN = "rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
"""Quiet opening position where the top three moves are all playable."""
MATE_FEN = "R5k1/5ppp/8/8/8/8/8/6K1 b - - 1 1"
ILLEGAL_FEN = "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1"
"""White to move with Black already in check: a position no game can reach. Stockfish exits
when it is asked about one, taking a pooled engine with it, so the coach must refuse it."""

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")


class StubBackend:
    """Fixed move probabilities, so the 'computer move' branch can be reached deliberately."""

    name: maia_service.Source = "maia"

    def __init__(self, probs: dict[str, float]) -> None:
        self.probs = probs

    def is_available(self) -> bool:
        return True

    def move_probs(
        self,
        fen: str,
        rating: int,
        include: Iterable[str] = (),
        opp_rating: int | None = None,
    ) -> dict[str, float]:
        return dict(self.probs)


@pytest.fixture
def no_maia() -> Iterator[None]:
    """Route move probabilities through Stockfish (or uniform sampling) instead of Maia-2."""
    backend = maia_service.EngineBackend()
    maia_service.use_backends(backend, maia_service.RandomBackend())
    yield
    maia_service.use_backends()
    backend.close()


def _stub(probs: dict[str, float]) -> Iterator[None]:
    maia_service.use_backends(StubBackend(probs))
    yield
    maia_service.use_backends()


def _claims(items: list[dict[str, object]]) -> list[Claim]:
    return [Claim.model_validate(item) for item in items]


def _all_hold(items: list[dict[str, object]]) -> bool:
    return all(v.holds for v in verify_all(_claims(items)))


def _hint(client: TestClient, **payload: object) -> dict[str, object]:
    res = client.post("/play/hint", json={"depth": DEPTH, **payload})
    assert res.status_code == 200, res.text
    return dict(res.json())


def _check(client: TestClient, **payload: object) -> dict[str, object]:
    res = client.post("/play/check", json={"depth": DEPTH, **payload})
    assert res.status_code == 200, res.text
    return dict(res.json())


# ---------- hint ----------


def test_level_1_names_the_structure_and_no_move(client: TestClient, no_maia: None) -> None:
    level1 = _hint(client, fen=MOCKUP_FEN, level=1, rating=1500)
    assert level1["side"] == "black"
    assert level1["structure"]["key"] != ""
    assert level1["candidates"] == [] and level1["best"] is None
    assert level1["source"] is None
    text = str(level1["text"])
    assert level1["structure"]["name"] in text

    # The moves level 2 would suggest are exactly what level 1 must not give away.
    level2 = _hint(client, fen=MOCKUP_FEN, level=2, rating=1500)
    suggested = [str(c["san"]) for c in level2["candidates"]]  # type: ignore[index,union-attr]
    assert suggested
    assert not [san for san in suggested if san in text], text


def test_level_1_marks_plans_already_executed(client: TestClient, no_maia: None) -> None:
    """The game so far comes from start_fen + moves_san, so a plan can read as executed."""
    body = _hint(
        client,
        fen="rnbqkb1r/pp2pppp/3p1n2/2pP4/2P5/2N5/PP2PPPP/R1BQKBNR b KQkq - 0 5",
        level=1,
        rating=1500,
        moves_san=["d4", "Nf6", "c4", "c5", "d5", "d6"],
    )
    assert {p["status"] for p in body["plans"]} <= {  # type: ignore[union-attr]
        "executed",
        "pv_match",
        "later",
        "unavailable",
    }
    assert body["text"]


def test_level_2_lists_natural_candidates_with_verified_reasons(
    client: TestClient, no_maia: None
) -> None:
    body = _hint(client, fen=MOCKUP_FEN, level=2, rating=1500)
    board = chess.Board(MOCKUP_FEN)
    legal = {board.san(mv) for mv in board.legal_moves}
    candidates = body["candidates"]
    assert isinstance(candidates, list) and 1 <= len(candidates) <= 3
    for cand in candidates:
        assert cand["san"] in legal
        assert board.parse_san(str(cand["san"])).uci() == cand["uci"]
        assert cand["prob"] is not None and cand["reason"]
        assert _all_hold(cand["claims"])
        assert str(cand["san"]) in str(body["text"])
    assert body["best"] is None
    assert body["source"] in {"maia", "engine", "random"}
    assert body["verified"] is True
    assert body["total_claims"] == body["verified_claims"] > 0


@needs_engine
def test_level_3_adds_the_engine_move_with_a_line(client: TestClient, no_maia: None) -> None:
    body = _hint(client, fen=MOCKUP_FEN, level=3, rating=1500)
    best = body["best"]
    assert isinstance(best, dict)
    board = chess.Board(MOCKUP_FEN)
    assert best["san"] == "exd5"  # winning the knight
    assert board.parse_san(str(best["san"])).uci() == best["uci"]
    assert best["pv"] and best["pv"][0] == best["san"]
    assert best["score"]["cp"] is not None or best["score"]["mate"] is not None
    assert best["reason"] and _all_hold(best["claims"])
    assert str(best["san"]) in str(body["text"])
    assert body["verified"] is True
    assert body["total_claims"] == body["verified_claims"] > 0


@needs_engine
def test_level_3_flags_a_move_no_human_plays(client: TestClient) -> None:
    """computer_move is the Maia probability of the engine's move, not an engine judgement."""
    board = chess.Board(MOCKUP_FEN)
    probs = {board.san(mv): 0.0 for mv in board.legal_moves}
    probs["exd5"] = 0.001
    probs["Rd8"] = 0.999
    stub = _stub(probs)
    next(stub)
    try:
        body = _hint(client, fen=MOCKUP_FEN, level=3, rating=1500)
    finally:
        next(stub, None)
    best = body["best"]
    assert isinstance(best, dict)
    assert best["san"] == "exd5" and best["computer_move"] is True


def test_hint_rejects_a_bad_fen_and_a_finished_game(client: TestClient, no_maia: None) -> None:
    assert client.post("/play/hint", json={"fen": "not a fen", "level": 1}).status_code == 422
    assert client.post("/play/hint", json={"fen": MATE_FEN, "level": 1}).status_code == 422


def test_hint_rejects_an_illegal_game_prefix(client: TestClient, no_maia: None) -> None:
    res = client.post(
        "/play/hint", json={"fen": MOCKUP_FEN, "level": 1, "moves_san": ["d4", "Nf7"]}
    )
    assert res.status_code == 422


# ---------- check ----------


@needs_engine
def test_check_calls_a_blunder_a_blunder(client: TestClient, no_maia: None) -> None:
    body = _check(client, fen_before=MOCKUP_FEN, san="Rd8", rating=1500)
    assert body["classification"] in {"mistake", "blunder"}
    assert float(body["win_loss"]) >= analysis._MISTAKE
    assert body["best_san"] == "exd5" and body["best_uci"] == "e6d5"
    assert body["pv"] and body["pv"][0] == "exd5"
    assert body["reason"] and body["verified"] is True
    assert _all_hold(body["claims"])
    assert body["eval_before"]["cp"] is not None and body["eval_after"]["cp"] is not None


@needs_engine
def test_check_accepts_the_best_move(client: TestClient, no_maia: None) -> None:
    body = _check(client, fen_before=MOCKUP_FEN, san="exd5", rating=1500)
    assert body["classification"] in {"best", "good"}
    assert float(body["win_loss"]) < analysis._GOOD
    assert body["san"] == "exd5" and body["best_san"] == "exd5"


@needs_engine
def test_check_marks_a_forced_move(client: TestClient, no_maia: None) -> None:
    body = _check(client, fen_before="7k/8/8/8/8/8/8/5q1K w - - 0 1", san="Kh2", rating=1500)
    assert body["classification"] == "forced"


@needs_engine
def test_check_offers_a_natural_alternative_to_a_computer_move(client: TestClient) -> None:
    """When nobody at this rating finds the engine's move, name one they would find."""
    lines = analysis.analyse_position(SICILIAN_FEN, DEPTH, 3)
    best, second, third = (line.pv[0] for line in lines[:3])
    loss = analysis.win_prob_loss(lines[0].score, lines[1].score, "black")
    assert loss < analysis._MISTAKE, "the second line must be playable for this test to mean much"

    stub = _stub({second: 0.8, third: 0.19, best: 0.004})
    next(stub)
    try:
        body = _check(client, fen_before=SICILIAN_FEN, san=third, rating=1500)
    finally:
        next(stub, None)
    assert body["computer_move"] is True
    assert body["alternative_san"] == second
    assert body["alternative_reason"]


@needs_engine
def test_check_without_a_computer_move_offers_no_alternative(
    client: TestClient, no_maia: None
) -> None:
    body = _check(client, fen_before=SICILIAN_FEN, san="Nc6", rating=1500)
    assert body["computer_move"] is False
    assert body["alternative_san"] is None and body["alternative_reason"] == ""


def test_check_rejects_a_bad_fen_and_an_illegal_move(client: TestClient, no_maia: None) -> None:
    assert client.post("/play/check", json={"fen_before": "nope", "san": "e4"}).status_code == 422
    res = client.post("/play/check", json={"fen_before": MOCKUP_FEN, "san": "Qxh8", "depth": DEPTH})
    assert res.status_code == 422


def test_hint_rejects_a_position_the_rules_forbid(client: TestClient, no_maia: None) -> None:
    """A rule-breaking FEN must never reach the engine: Stockfish dies on it (the process
    exits and the pooled engine is thrown away), which used to answer 500."""
    res = client.post("/play/hint", json={"fen": ILLEGAL_FEN, "level": 3, "depth": DEPTH})
    assert res.status_code == 422
    assert "규칙" in res.json()["detail"]


def test_check_rejects_a_position_the_rules_forbid(client: TestClient, no_maia: None) -> None:
    res = client.post(
        "/play/check", json={"fen_before": ILLEGAL_FEN, "san": "Rxe8", "depth": DEPTH}
    )
    assert res.status_code == 422
    assert "규칙" in res.json()["detail"]


def test_a_busy_pool_is_503_not_500(client: TestClient, no_maia: None) -> None:
    """Every engine checked out is a wait, not a fault; /analysis/position already says so."""
    import chess_tutor.services.play_coach as coach
    from chess_tutor.engine import EngineBusy

    class _Busy:
        def borrow(self) -> object:
            raise EngineBusy

    original = coach.pool
    coach.pool = _Busy()  # type: ignore[assignment]
    try:
        res = client.post("/play/check", json={"fen_before": SICILIAN_FEN, "san": "Nc6"})
    finally:
        coach.pool = original
    assert res.status_code == 503
