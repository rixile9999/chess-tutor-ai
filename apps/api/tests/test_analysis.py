"""Engine analysis pipeline: classification arithmetic, PGN walking, whole-game analysis,
the EngineCache, and the /analysis endpoints. Engine tests stay at depth <= 8 on short games."""

from __future__ import annotations

import chess
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db, models, schemas
from chess_tutor.engine import find_stockfish
from chess_tutor.jobs import runner
from chess_tutor.services import analysis as svc

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")

# 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6?? 4.Qxf7#  (3...Nf6 allows mate in one)
SCHOLARS_MATE = "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0"
# 5.Qe3?? hangs the queen to the c5 bishop
HANGING_QUEEN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Qe2 Bc5 5. Qe3 Bxe3 6. fxe3 *"


def _fens(pgn: str) -> list[str]:
    start, plies = svc.walk_pgn(pgn)
    return [start.fen()] + [p.fen_after for p in plies]


# ---------- pure arithmetic ----------


def test_win_prob_is_symmetric_and_mate_aware() -> None:
    even = schemas.Score(cp=0)
    assert svc.win_prob(even, "white") == pytest.approx(0.5)
    assert svc.win_prob(even, "black") == pytest.approx(0.5)
    plus = schemas.Score(cp=300)
    assert svc.win_prob(plus, "white") + svc.win_prob(plus, "black") == pytest.approx(1.0)
    assert svc.win_prob(plus, "white") > 0.7
    assert svc.win_prob(schemas.Score(mate=2), "white") == 1.0
    assert svc.win_prob(schemas.Score(mate=2), "black") == 0.0
    assert svc.win_prob(schemas.Score(mate=-1), "black") == 1.0


def test_classification_thresholds_and_accuracy_curve() -> None:
    assert svc.classify_loss(0.0) == "best"
    assert svc.classify_loss(0.004) == "best"
    assert svc.classify_loss(0.01) == "good"
    assert svc.classify_loss(0.03) == "inaccuracy"
    assert svc.classify_loss(0.1) == "mistake"
    assert svc.classify_loss(0.3) == "blunder"
    assert svc.move_accuracy(0.0) == pytest.approx(100.0, abs=0.01)
    assert svc.move_accuracy(1.0) == 0.0
    assert 0.0 < svc.move_accuracy(0.1) < svc.move_accuracy(0.02) < 100.0
    # a move that is worse for the mover than the best line costs probability; never a bonus
    loss = svc.win_prob_loss(schemas.Score(cp=50), schemas.Score(cp=-250), "white")
    assert loss > 0.15
    assert svc.win_prob_loss(schemas.Score(cp=50), schemas.Score(cp=80), "white") == 0.0
    # same drop, seen from Black
    assert svc.win_prob_loss(
        schemas.Score(cp=-50), schemas.Score(cp=250), "black"
    ) == pytest.approx(loss)


def test_terminal_scores_follow_the_board() -> None:
    board = chess.Board()
    for san in ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]:
        board.push_san(san)
    assert board.is_checkmate()
    mated = svc.terminal_score(board)
    assert mated is not None and mated.cp == svc.MATE_CP and mated.as_pawns() == 100.0
    stalemate = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert stalemate.is_stalemate()
    assert svc.terminal_score(stalemate) == schemas.Score(cp=0)
    assert svc.terminal_score(chess.Board()) is None


# ---------- PGN walk ----------


def test_walk_pgn_reads_clocks_and_book_flags() -> None:
    pgn = '[White "a"]\n[Black "b"]\n\n1. e4 {[%clk 0:05:00]} e5 {[%clk 0:04:58.5]} 2. Nf3 *'
    start, plies = svc.walk_pgn(pgn)
    assert start == chess.Board()
    assert [p.san for p in plies] == ["e4", "e5", "Nf3"]
    assert [p.clock for p in plies] == [300.0, 298.5, None]
    assert [p.color for p in plies] == ["white", "black", "white"]
    assert plies[0].fen_before == chess.STARTING_FEN
    assert chess.Board(plies[2].fen_after).piece_at(chess.F3) == chess.Piece.from_symbol("N")
    assert plies[0].legal_moves == 20


def test_walk_pgn_rejects_illegal_moves_and_empty_input() -> None:
    with pytest.raises(ValueError):
        svc.walk_pgn("1. e4 e5 2. Ke2 Nc6 3. Bc4 *")
    with pytest.raises(ValueError):
        svc.walk_pgn("")


def test_book_flags_stop_at_the_first_unknown_position() -> None:
    start, plies = svc.walk_pgn(HANGING_QUEEN)
    flags = svc._book_flags(start, plies)
    # 1.e4 ... 3...Nf6 is the Two Knights Defense; 4.Qe2 leaves the book
    assert flags == [True] * 6 + [False] * 5


# ---------- whole-game analysis (engine) ----------


@needs_engine
def test_scholars_mate_is_a_blunder_and_the_mate_is_best() -> None:
    game = models.Game(pgn=SCHOLARS_MATE, source="pgn", white="a", black="b", result="1-0")
    summary, moves = svc.analyze_game(game, depth=8, multipv=3)

    assert [m.san for m in moves] == ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]
    assert [m.classification for m in moves[:3]] == ["book", "book", "book"]
    assert moves[5].color == "black"
    assert moves[5].classification in ("mistake", "blunder")
    assert moves[5].win_prob_loss >= 0.06
    # the engine saw the mate before 3...Nf6 was answered
    assert moves[5].eval_after.mate == 1
    assert moves[6].best_move_san == "Qxf7#"
    assert moves[6].classification == "best"
    assert moves[6].eval_before.mate == 1
    assert moves[6].eval_after.as_pawns() == 100.0

    assert len(summary.eval_series) == len(moves) + 1
    assert summary.eval_series[-1] == 10.0
    assert all(-10.0 <= v <= 10.0 for v in summary.eval_series)
    assert 0.0 <= summary.accuracy_white <= 100.0
    assert 0.0 <= summary.accuracy_black <= 100.0
    assert summary.accuracy_white > summary.accuracy_black
    assert sum(summary.counts["white"].values()) == 4
    assert sum(summary.counts["black"].values()) == 3
    assert summary.counts["black"]["mistake"] + summary.counts["black"]["blunder"] == 1
    assert set(summary.counts["white"]) == set(svc.CLASSIFICATIONS)

    # every line is a legal continuation of the position it was computed for
    for move in moves:
        for line in move.lines:
            board = chess.Board(move.fen_before)
            for san, uci in zip(line.pv, line.pv_uci, strict=True):
                assert board.parse_san(san) == chess.Move.from_uci(uci)
                board.push_san(san)


@needs_engine
def test_hanging_the_queen_is_a_blunder_and_taking_it_is_best() -> None:
    game = models.Game(pgn=HANGING_QUEEN, source="pgn", white="a", black="b", result="*")
    summary, moves = svc.analyze_game(game, depth=8, multipv=2)

    by_ply = {m.ply: m for m in moves}
    assert by_ply[9].san == "Qe3" and by_ply[9].classification == "blunder"
    assert by_ply[9].win_prob_loss >= 0.15
    assert by_ply[9].best_move_uci != "e2e3"
    assert (
        chess.Move.from_uci(by_ply[9].best_move_uci or "")
        in chess.Board(by_ply[9].fen_before).legal_moves
    )
    assert by_ply[10].san == "Bxe3" and by_ply[10].classification == "best"
    assert by_ply[10].win_prob_loss == 0.0
    # eval after the blunder favours Black by a lot, and the series tracks it
    assert by_ply[9].eval_after.as_pawns() < -3.0
    assert summary.eval_series[9] < -3.0
    assert len(summary.eval_series) == 12
    assert summary.counts["white"]["blunder"] == 1
    assert summary.accuracy_black > summary.accuracy_white
    # book moves are still reported with their engine lines and evaluations
    assert by_ply[1].classification == "book" and by_ply[1].lines


@needs_engine
async def test_get_lines_hits_the_engine_cache_on_the_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = chess.Board()
    for san in ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6"]:
        board.push_san(san)
    fen = board.fen()
    first = await svc.get_lines(fen, depth=6, multipv=2)
    assert first[0].score.mate == 1 and first[0].pv[0] == "Qxf7#"

    async with db.session_factory()() as session:
        rows = (await session.execute(select(models.EngineCache))).scalars().all()
    assert len(rows) == 1
    assert (rows[0].fen, rows[0].depth, rows[0].multipv) == (fen, 6, 2)
    assert rows[0].engine.startswith("stockfish")

    def boom(*_: object, **__: object) -> list[schemas.EngineLine]:
        raise AssertionError("engine must not run on a cache hit")

    monkeypatch.setattr(svc, "analyse_position", boom)
    second = await svc.get_lines(fen, depth=6, multipv=2)
    assert second == first


# ---------- API ----------


async def _insert_game(pgn: str, source_id: str) -> int:
    async with db.session_factory()() as session:
        game = models.Game(source="pgn", source_id=source_id, pgn=pgn, white="a", black="b")
        session.add(game)
        await session.commit()
        return game.id


@needs_engine
async def test_analysis_api_flow(aclient: AsyncClient) -> None:
    game_id = await _insert_game(SCHOLARS_MATE, "api-flow")

    res = await aclient.get(f"/analysis/{game_id}")
    assert res.status_code == 200 and res.json()["status"] == "none"
    assert (await aclient.get(f"/analysis/{game_id + 100}")).status_code == 404
    assert (await aclient.post(f"/analysis/{game_id + 100}")).status_code == 404

    res = await aclient.post(f"/analysis/{game_id}", params={"depth": 8, "multipv": 2})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "pending" and body["depth"] == 8 and body["moves"] == []

    job = await runner.wait(svc.job_key(game_id), timeout=60)
    assert job.status == "done", job.error

    res = await aclient.get(f"/analysis/{game_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "done" and body["error"] is None
    assert body["engine"].startswith("stockfish") and body["depth"] == 8
    assert len(body["moves"]) == 7
    assert body["moves"][5]["classification"] in ("mistake", "blunder")
    assert len(body["summary"]["eval_series"]) == 8
    assert 0 <= body["summary"]["accuracy_black"] <= 100

    # the game's positions are now cached, so the same analysis at the same depth reuses them
    async with db.session_factory()() as session:
        cached = (await session.execute(select(models.EngineCache))).scalars().all()
    assert {row.fen for row in cached} == set(_fens(SCHOLARS_MATE))

    # get_or_analyze returns the stored result without touching the engine
    got = await svc.get_or_analyze(game_id)
    assert got.status == "done" and len(got.moves) == 7


@needs_engine
async def test_get_or_analyze_runs_inline_when_nothing_is_stored() -> None:
    game_id = await _insert_game(SCHOLARS_MATE, "inline")
    got = await svc.get_or_analyze(game_id, depth=6, multipv=2)
    assert got.status == "done" and got.depth == 6
    assert [m.san for m in got.moves][-1] == "Qxf7#"
    assert (await svc.get_analysis(game_id)).status == "done"


async def test_run_analysis_records_pgn_errors_on_the_row() -> None:
    # 2.Ke2 is legal; 3.Bc4 is not (the king blocks the f1 bishop)
    game_id = await _insert_game("1. e4 e5 2. Ke2 Nc6 3. Bc4 *", "broken")
    if find_stockfish() is None:
        pytest.skip("stockfish binary not available")
    got = await svc.run_analysis(game_id, depth=4, multipv=1)
    assert got.status == "failed"
    assert got.error and "PGN" in got.error
    assert (await svc.get_analysis(game_id)).status == "failed"


@needs_engine
async def test_position_endpoint_returns_lines(aclient: AsyncClient) -> None:
    board = chess.Board()
    for san in ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6"]:
        board.push_san(san)
    res = await aclient.post(
        "/analysis/position", json={"fen": board.fen(), "depth": 6, "multipv": 2}
    )
    assert res.status_code == 200
    lines = res.json()
    assert lines[0]["rank"] == 1 and lines[0]["score"]["mate"] == 1
    assert lines[0]["pv"][0] == "Qxf7#" and lines[0]["pv_uci"][0] == "h5f7"

    res = await aclient.post("/analysis/position", json={"fen": "not a fen"})
    assert res.status_code == 422

    # a finished position needs no engine: one line, empty pv, score from the board
    board.push_san("Qxf7#")
    res = await aclient.post("/analysis/position", json={"fen": board.fen(), "depth": 6})
    assert res.status_code == 200
    assert res.json() == [
        {"rank": 1, "score": {"cp": svc.MATE_CP, "mate": None}, "pv": [], "pv_uci": []}
    ]
