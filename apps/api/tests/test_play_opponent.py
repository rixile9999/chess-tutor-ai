"""The practice opponent: Maia at a rating, Stockfish at a UCI_Elo, and the fallback between.

Nothing here imports torch: the Maia backend is replaced by the engine/random chain or by a
stub, the way tests/test_maia.py does it.
"""

from collections.abc import Iterable, Iterator

import chess
import pytest
from fastapi.testclient import TestClient

from chess_tutor.engine import find_stockfish
from chess_tutor.services import maia as maia_service
from chess_tutor.services import play_opponent

MOCKUP_FEN = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
MATE_FEN = "R5k1/5ppp/8/8/8/8/8/6K1 b - - 1 1"
"""Black to move after Ra8#: a finished game, so no opponent move exists."""
ILLEGAL_FEN = "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1"
"""White to move with Black already in check: unreachable, and Stockfish exits on it."""

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")


class RecordingBackend:
    """A backend that answers with a fixed distribution and remembers how it was called."""

    name: maia_service.Source = "maia"

    def __init__(self, probs: dict[str, float] | None = None) -> None:
        self.probs = probs
        self.calls: list[tuple[int, int | None]] = []

    def is_available(self) -> bool:
        return True

    def move_probs(
        self,
        fen: str,
        rating: int,
        include: Iterable[str] = (),
        opp_rating: int | None = None,
    ) -> dict[str, float]:
        self.calls.append((rating, opp_rating))
        if self.probs is not None:
            return dict(self.probs)
        board = chess.Board(fen)
        moves = list(board.legal_moves)
        return {board.san(mv): 1.0 / len(moves) for mv in moves}


@pytest.fixture
def no_maia() -> Iterator[None]:
    """Route the service through Stockfish (or uniform sampling) instead of Maia-2."""
    backend = maia_service.EngineBackend()
    maia_service.use_backends(backend, maia_service.RandomBackend())
    yield
    maia_service.use_backends()
    backend.close()


@pytest.fixture
def uniform() -> Iterator[None]:
    """Uniform sampling only: no engine process, so seeding is the only source of variation."""
    maia_service.use_backends(maia_service.RandomBackend())
    yield
    maia_service.use_backends()


@pytest.fixture
def recording() -> Iterator[RecordingBackend]:
    backend = RecordingBackend()
    maia_service.use_backends(backend)
    yield backend
    maia_service.use_backends()


@pytest.fixture
def closed_play_engine() -> Iterator[None]:
    """Quit the private engine afterwards so the next test starts from a known state."""
    yield
    play_opponent.close_engine()


def _legal_sans(fen: str) -> set[str]:
    board = chess.Board(fen)
    return {board.san(mv) for mv in board.legal_moves}


# ---------- clamping ----------


def test_ratings_are_clamped_to_each_backend_range() -> None:
    clamp = play_opponent.clamp
    assert clamp(800, play_opponent.MAIA_MIN, play_opponent.MAIA_MAX) == 1100
    assert clamp(2600, play_opponent.MAIA_MIN, play_opponent.MAIA_MAX) == 2000
    assert clamp(1500, play_opponent.MAIA_MIN, play_opponent.MAIA_MAX) == 1500
    assert clamp(800, play_opponent.STOCKFISH_MIN, play_opponent.STOCKFISH_MAX) == 1320
    assert clamp(3400, play_opponent.STOCKFISH_MIN, play_opponent.STOCKFISH_MAX) == 3190


def test_maia_rating_reaches_the_backend_clamped(
    client: TestClient, recording: RecordingBackend
) -> None:
    res = client.post(
        "/play/move",
        json={"fen": MOCKUP_FEN, "opponent": {"kind": "maia", "rating": 2500}, "user_rating": 1200},
    )
    assert res.status_code == 200
    assert recording.calls == [(2000, 1200)]


def test_opponent_rating_and_user_rating_are_passed_separately(
    client: TestClient, recording: RecordingBackend
) -> None:
    """Maia-2 conditions on both players: self is the opponent, opp is the human."""
    res = client.post(
        "/play/move",
        json={"fen": MOCKUP_FEN, "opponent": {"kind": "maia", "rating": 1500}, "user_rating": 1800},
    )
    assert res.status_code == 200
    assert recording.calls == [(1500, 1800)]


def test_missing_user_rating_leaves_the_old_call_shape(
    client: TestClient, recording: RecordingBackend
) -> None:
    """Without a user rating nothing extra is passed, so a three-argument backend still works."""
    res = client.post("/play/move", json={"fen": MOCKUP_FEN})
    assert res.status_code == 200
    assert recording.calls == [(1500, None)]


# ---------- maia kind ----------


def test_move_endpoint_returns_a_legal_move_with_probabilities(
    client: TestClient, no_maia: None
) -> None:
    res = client.post(
        "/play/move", json={"fen": MOCKUP_FEN, "opponent": {"kind": "maia", "rating": 1500}}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["san"] in _legal_sans(MOCKUP_FEN)
    assert chess.Board(MOCKUP_FEN).parse_san(body["san"]).uci() == body["uci"]
    assert body["source"] == ("engine" if find_stockfish() else "random")
    assert sum(body["probs"].values()) == pytest.approx(1.0, abs=1e-3)
    assert body["think_ms"] >= 0


def test_the_same_seed_picks_the_same_move(client: TestClient, uniform: None) -> None:
    payload = {"fen": MOCKUP_FEN, "opponent": {"kind": "maia", "rating": 1500}, "seed": 7}
    first = client.post("/play/move", json=payload).json()
    second = client.post("/play/move", json=payload).json()
    assert first["san"] == second["san"]
    others = {
        client.post("/play/move", json={**payload, "seed": seed}).json()["san"]
        for seed in range(20)
    }
    assert len(others) > 1, "every seed produced the same move"


def test_bad_fen_is_422(client: TestClient, uniform: None) -> None:
    res = client.post("/play/move", json={"fen": "not a fen"})
    assert res.status_code == 422


def test_finished_game_is_422(client: TestClient, uniform: None) -> None:
    res = client.post("/play/move", json={"fen": MATE_FEN})
    assert res.status_code == 422


# ---------- stockfish kind ----------


@needs_engine
def test_stockfish_kind_returns_a_legal_move(client: TestClient, closed_play_engine: None) -> None:
    res = client.post(
        "/play/move", json={"fen": MOCKUP_FEN, "opponent": {"kind": "stockfish", "rating": 1800}}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["san"] in _legal_sans(MOCKUP_FEN)
    assert body["source"] == "stockfish"
    assert body["probs"] == {}


@needs_engine
def test_stockfish_elo_is_clamped_and_only_reconfigured_when_it_changes(
    closed_play_engine: None,
) -> None:
    engine = play_opponent.get_engine()
    low, high = engine.elo_range()
    board = chess.Board(MOCKUP_FEN)
    engine.play(board, movetime=0.05, elo=100)
    assert engine._elo == low
    engine.play(board, movetime=0.05, elo=9000)
    assert engine._elo == high


@needs_engine
def test_stockfish_finished_game_is_422(client: TestClient, closed_play_engine: None) -> None:
    res = client.post(
        "/play/move", json={"fen": MATE_FEN, "opponent": {"kind": "stockfish", "rating": 1800}}
    )
    assert res.status_code == 422


def test_stockfish_falls_back_to_the_human_backend_when_the_binary_is_missing(
    client: TestClient, uniform: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine without Stockfish still answers the move, and says who really played it."""

    def missing() -> None:
        raise RuntimeError("Stockfish not found")

    monkeypatch.setattr(play_opponent, "get_engine", missing)
    res = client.post(
        "/play/move", json={"fen": MOCKUP_FEN, "opponent": {"kind": "stockfish", "rating": 1800}}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "random"
    assert body["san"] in _legal_sans(MOCKUP_FEN)


def test_position_the_rules_forbid_is_422(client: TestClient, uniform: None) -> None:
    """Neither opponent may be asked about a position no game can reach."""
    for kind in ("maia", "stockfish"):
        res = client.post(
            "/play/move", json={"fen": ILLEGAL_FEN, "opponent": {"kind": kind, "rating": 1500}}
        )
        assert res.status_code == 422, res.text
        assert "규칙" in res.json()["detail"]


def test_a_dead_engine_does_not_close_its_replacement() -> None:
    """Two games at once: one engine dies, another request has already started a new one.
    Closing must then take down only the dead process, not the fresh one."""

    class _Fake:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    dead, fresh = _Fake(), _Fake()
    play_opponent._engine = fresh  # type: ignore[assignment]
    try:
        play_opponent.close_engine(dead)  # type: ignore[arg-type]
        assert dead.closed and not fresh.closed
        assert play_opponent._engine is fresh  # type: ignore[comparison-overlap]
        play_opponent.close_engine(fresh)  # type: ignore[arg-type]
        assert fresh.closed and play_opponent._engine is None
    finally:
        play_opponent._engine = None
