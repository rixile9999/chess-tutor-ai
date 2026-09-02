"""Human move model: engine fallback distribution, natural_reason facts, endpoints.

Independent of maia2: every test forces the engine (or random) backend, so nothing here
imports torch or downloads weights.
"""

from collections.abc import Iterator

import chess
import pytest
from fastapi.testclient import TestClient

from chess_tutor.engine import find_stockfish
from chess_tutor.services import maia as maia_service
from chess_tutor.verify import verify_all

MOCKUP_FEN = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
"""Mockup review position: Black has just been hit by Nd5 and answered 20...Qd7."""

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")


@pytest.fixture(scope="module")
def engine_backend() -> Iterator[maia_service.EngineBackend]:
    backend = maia_service.EngineBackend()
    yield backend
    backend.close()


@pytest.fixture
def no_maia(engine_backend: maia_service.EngineBackend) -> Iterator[None]:
    """Route the service through Stockfish (or uniform sampling) instead of Maia-2."""
    maia_service.use_backends(engine_backend, maia_service.RandomBackend())
    yield
    maia_service.use_backends()


class FailingBackend:
    name = "maia"

    def is_available(self) -> bool:
        return True

    def move_probs(self, fen: str, rating: int, include=()) -> dict[str, float]:
        raise maia_service.BackendUnavailable("weights missing")


def _legal_sans(fen: str) -> set[str]:
    board = chess.Board(fen)
    return {board.san(mv) for mv in board.legal_moves}


# ---------- temperature and distributions ----------


def test_temperature_grows_as_rating_drops() -> None:
    assert maia_service.temperature(1500) == pytest.approx(80.0)
    assert maia_service.temperature(2200) == pytest.approx(30.0)
    ratings = [800, 1000, 1200, 1500, 1800, 2200, 2600]
    temps = [maia_service.temperature(r) for r in ratings]
    assert temps == sorted(temps, reverse=True)
    assert all(12.0 <= t <= 200.0 for t in temps)


@needs_engine
def test_engine_probs_are_a_distribution_over_legal_moves(
    engine_backend: maia_service.EngineBackend,
) -> None:
    probs = engine_backend.move_probs(MOCKUP_FEN, 1500)
    assert set(probs) == _legal_sans(MOCKUP_FEN)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(p > 0 for p in probs.values())
    values = list(probs.values())
    assert values == sorted(values, reverse=True)


@needs_engine
def test_engine_probs_flatten_at_lower_rating(
    engine_backend: maia_service.EngineBackend,
) -> None:
    strong = engine_backend.move_probs(MOCKUP_FEN, 2200)
    weak = engine_backend.move_probs(MOCKUP_FEN, 1000)
    top = next(iter(strong))
    assert top == next(iter(weak))
    assert weak[top] < strong[top]


@needs_engine
def test_engine_probs_include_evaluates_the_requested_move(
    engine_backend: maia_service.EngineBackend,
) -> None:
    board = chess.Board(MOCKUP_FEN)
    scores = engine_backend.scores(board, include=["Qd7"])
    assert board.parse_san("Qd7") in scores
    # 20...Qd7 loses the queen to 21.Nxf6+ (discovered attack down the d-file), so the
    # engine scores it far below the best line even at depth 8.
    assert scores[board.parse_san("Qd7")] < max(scores.values()) - 300


def test_random_backend_is_uniform() -> None:
    probs = maia_service.RandomBackend().move_probs(chess.STARTING_FEN, 1500)
    assert set(probs) == _legal_sans(chess.STARTING_FEN)
    assert all(p == pytest.approx(1 / 20) for p in probs.values())


def test_move_probs_falls_back_when_a_backend_fails() -> None:
    maia_service.use_backends(FailingBackend(), maia_service.RandomBackend())
    try:
        probs, source = maia_service.move_probs(chess.STARTING_FEN, 1500)
    finally:
        maia_service.use_backends()
    assert source == "random"
    assert sum(probs.values()) == pytest.approx(1.0)


def test_choose_move_is_reproducible_with_a_seed() -> None:
    backend = maia_service.RandomBackend()
    first = maia_service.choose_move(chess.STARTING_FEN, 1500, seed=7, backend=backend)
    second = maia_service.choose_move(chess.STARTING_FEN, 1500, seed=7, backend=backend)
    assert first == second
    san, uci, probs, source = first
    board = chess.Board()
    assert board.parse_san(san).uci() == uci
    assert source == "random"
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-4)


@needs_engine
def test_choose_move_with_engine(engine_backend: maia_service.EngineBackend) -> None:
    san, uci, probs, source = maia_service.choose_move(
        MOCKUP_FEN, 1500, seed=3, backend=engine_backend
    )
    assert source == "engine"
    assert san in _legal_sans(MOCKUP_FEN)
    assert chess.Board(MOCKUP_FEN).parse_san(san).uci() == uci
    assert probs[san] > 0


def test_choose_move_rejects_finished_games() -> None:
    board = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
    board.push_san("Ra8#")
    mated = board.fen()
    with pytest.raises(ValueError):
        maia_service.choose_move(mated, 1500, backend=maia_service.RandomBackend())


# ---------- human view ----------


@needs_engine
def test_human_view_on_mockup_position(engine_backend: maia_service.EngineBackend) -> None:
    view = maia_service.human_view(MOCKUP_FEN, "Qd7", "Nxd5", 1500, backend=engine_backend)
    assert view.rating == 1500
    assert view.source == "engine"
    assert view.played_prob is not None and 0.0 <= view.played_prob <= 1.0
    assert "Qd7" in view.move_probs and "Nxd5" in view.move_probs
    assert view.played_prob == pytest.approx(view.move_probs["Qd7"])
    assert view.natural_reason is not None
    assert "지켰" in view.natural_reason
    assert "e7 비숍" in view.natural_reason
    assert "Nd5" in view.natural_reason
    kinds = {c.kind for c in view.claims}
    assert {"attacks", "defends", "legal_move"} <= kinds
    assert all(v.holds for v in verify_all(view.claims))


def test_human_view_rejects_illegal_moves() -> None:
    with pytest.raises(ValueError):
        maia_service.human_view(
            MOCKUP_FEN, "Qh1", "Nxd5", 1500, backend=maia_service.RandomBackend()
        )
    with pytest.raises(ValueError):
        maia_service.human_view("not a fen", "e4", None, 1500, backend=maia_service.RandomBackend())


def test_human_view_flags_computer_move_when_best_is_improbable() -> None:
    board = chess.Board(MOCKUP_FEN)
    best = board.san(board.parse_san("Nxd5"))

    class Skewed:
        name = "engine"

        def is_available(self) -> bool:
            return True

        def move_probs(self, fen: str, rating: int, include=()) -> dict[str, float]:
            sans = sorted(_legal_sans(fen))
            rest = [s for s in sans if s != best]
            probs = {s: 0.99 / len(rest) for s in rest}
            probs[best] = 0.01
            return dict(sorted(probs.items(), key=lambda kv: -kv[1]))

    view = maia_service.human_view(MOCKUP_FEN, "Qd7", "Nxd5", 1500, backend=Skewed())
    assert view.computer_move is True
    assert view.move_probs[best] == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("fen", "san", "last_san", "expect"),
    [
        # 1.e4 d5 2.exd5: a plain capture.
        ("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2", "exd5", None, "잡았"),
        # ... 2...Qxd5 right after exd5 is a recapture.
        ("rnbqkbnr/ppp1pppp/8/3P4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2", "Qxd5", "exd5", "되잡"),
        ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", "Rh8+", None, "체크를 걸었"),
        ("4k3/8/8/8/8/8/8/4K2R w K - 0 1", "O-O", None, "킹사이드 캐슬링"),
        (chess.STARTING_FEN, "Nf3", None, "전개"),
        (chess.STARTING_FEN, "e4", None, "밀었"),
        # Knight on e5 attacked by the d6 pawn steps away.
        ("4k3/8/3p4/4N3/8/8/8/4K3 w - - 0 1", "Nf3", None, "피했"),
        # Rook swings to a1 and hits the undefended bishop.
        ("4k3/b7/8/8/8/8/4K3/7R w - - 0 1", "Ra1", None, "a7 비숍을 공격"),
        ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a8=Q+", None, "승격"),
        ("4k3/8/8/8/8/8/8/4K2r w - - 0 1", "Kd2", None, "체크를 받은 킹"),
        ("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "Ra8#", None, "체크메이트"),
        # Rc8 attacks the undefended knight on c1; the bishop interposes on c2.
        ("2r1k3/8/8/5B2/8/8/8/2N1K3 w - - 0 1", "Bc2", None, "사이를 막았"),
        # Rc8 attacks the undefended knight on c1; the king steps over to cover it.
        ("2r1k3/8/8/8/8/8/8/2N1K3 w - - 0 1", "Kd2", None, "지켰"),
    ],
)
def test_natural_reason_cases(fen: str, san: str, last_san: str | None, expect: str) -> None:
    board = chess.Board(fen)
    text, claims = maia_service.natural_reason(board, board.parse_san(san), last_san=last_san)
    assert expect in text, text
    assert "—" not in text
    assert claims and all(v.holds for v in verify_all(claims)), text


def test_natural_reason_mockup_matches_mockup_copy() -> None:
    board = chess.Board(MOCKUP_FEN)
    text, _ = maia_service.natural_reason(board, board.parse_san("Qd7"))
    assert text == "Nd5가 e7 비숍을 공격하자 퀸으로 지켰습니다."


# ---------- endpoints ----------


def test_status_endpoint(client: TestClient, no_maia: None) -> None:
    res = client.get("/maia/status")
    assert res.status_code == 200
    body = res.json()
    assert set(body) >= {"maia2_available", "backend", "maia_loaded", "stockfish_available"}
    assert body["maia2_available"] is False
    assert body["backend"] == ("engine" if find_stockfish() else "random")
    assert client.get("/maia/_status").status_code == 200


def test_move_endpoint(client: TestClient, no_maia: None) -> None:
    res = client.post("/maia/move", json={"fen": MOCKUP_FEN, "rating": 1500})
    assert res.status_code == 200
    body = res.json()
    assert body["san"] in _legal_sans(MOCKUP_FEN)
    assert chess.Board(MOCKUP_FEN).parse_san(body["san"]).uci() == body["uci"]
    assert body["source"] == ("engine" if find_stockfish() else "random")
    assert sum(body["probs"].values()) == pytest.approx(1.0, abs=1e-3)


def test_probs_endpoint(client: TestClient, no_maia: None) -> None:
    res = client.post("/maia/probs", json={"fen": chess.STARTING_FEN, "rating": 1200})
    assert res.status_code == 200
    body = res.json()
    assert body["rating"] == 1200
    assert set(body["move_probs"]) == _legal_sans(chess.STARTING_FEN)
    assert sum(body["move_probs"].values()) == pytest.approx(1.0, abs=1e-3)
    assert body["source"] in ("engine", "random")


def test_endpoints_reject_bad_input(client: TestClient, no_maia: None) -> None:
    assert client.post("/maia/move", json={"fen": "garbage"}).status_code == 422
    mated = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
    mated.push_san("Ra8#")
    assert client.post("/maia/move", json={"fen": mated.fen()}).status_code == 422
