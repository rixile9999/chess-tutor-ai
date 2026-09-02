"""Engine analysis pipeline: classification arithmetic, PGN walking, whole-game analysis,
the EngineCache, and the /analysis endpoints. Engine tests stay at depth <= 8 on short games,
except the five-ply Opera finish, which needs depth 12 to show the horizon effect at all."""

from __future__ import annotations

import asyncio

import chess
import chess.engine
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db, models, schemas
from chess_tutor import engine as engine_mod
from chess_tutor.config import Settings
from chess_tutor.engine import find_stockfish
from chess_tutor.jobs import Job, JobRunner, runner
from chess_tutor.routers import analysis as analysis_router
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
    assert svc.win_prob(schemas.Score(mate=2), "white") == pytest.approx(1.0, abs=1e-8)
    assert svc.win_prob(schemas.Score(mate=2), "black") == pytest.approx(0.0, abs=1e-8)
    assert svc.win_prob(schemas.Score(mate=-1), "black") == pytest.approx(1.0, abs=1e-8)
    # a mate beats any centipawn score, and a delivered mate beats an announced one
    assert svc.win_prob(schemas.Score(mate=30), "white") > svc.win_prob(schemas.Score(cp=3000))
    assert svc.win_prob(schemas.Score(cp=svc.MATE_CP)) > svc.win_prob(schemas.Score(mate=1))


def test_win_prob_keeps_the_distance_to_mate() -> None:
    """A collapsed 1.0/0.0 makes every move of a lost player 'best' at 100 accuracy and hides a
    mate that was pushed further away; the score has to carry the distance."""
    closer, further = schemas.Score(mate=1), schemas.Score(mate=12)
    assert svc.win_prob(closer, "white") > svc.win_prob(further, "white")
    assert svc.win_prob(closer, "black") < svc.win_prob(further, "black")
    assert svc.win_prob_loss(closer, further, "white") > 0.0
    assert svc.win_prob_loss(further, closer, "white") == 0.0
    # being mated later is better than being mated sooner, from the mated side
    assert svc.win_prob_loss(schemas.Score(mate=-6), schemas.Score(mate=-1), "white") > 0.0
    assert svc.mate_cp(1) == svc.MATE_CP - svc.MATE_STEP_CP
    assert svc.mate_cp(-1) == -svc.mate_cp(1)
    assert svc.mate_cp(80) == svc.mate_cp(50)  # distance saturates instead of going negative


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


# ---------- classification from crafted lines (no engine) ----------


def _line(
    rank: int, uci: str, san: str, cp: int | None = None, mate: int | None = None
) -> schemas.EngineLine:
    return schemas.EngineLine(
        rank=rank, score=schemas.Score(cp=cp, mate=mate), pv=[san], pv_uci=[uci]
    )


def _quiet(start: chess.Board, plies: list[svc.Ply]) -> dict[str, list[schemas.EngineLine]]:
    """Every position dead level, with a best move nobody played."""
    fens = [start.fen()] + [p.fen_after for p in plies]
    return {fen: [_line(1, "a2a3", "a3", cp=20)] for fen in fens}


def test_played_move_is_valued_from_the_parent_line_not_the_child_search() -> None:
    """The child position is searched to the same depth as its parent, so it sees one ply
    further and its score is not comparable. When the engine already ranked the played move in
    the parent position, that line's score is the number to use."""
    start, plies = svc.walk_pgn(HANGING_QUEEN)
    target = plies[6]  # 4.Qe2, the first move out of book
    assert (target.san, target.uci, target.color) == ("Qe2", "d1e2", "white")

    lines = _quiet(start, plies)
    lines[target.fen_before] = [_line(1, "f1e1", "Re1", cp=60), _line(2, "d1e2", "Qe2", cp=50)]
    lines[target.fen_after] = [_line(1, "c6d4", "Nd4", cp=-400)]

    _, moves = svc.build_analysis(start, plies, lines)
    assert moves[6].eval_after == schemas.Score(cp=50)
    assert moves[6].classification == "good"
    assert moves[6].win_prob_loss < 0.02
    # the child search alone would have called the same move a blunder
    assert svc.win_prob_loss(schemas.Score(cp=60), schemas.Score(cp=-400), "white") > 0.15


def test_a_harsh_verdict_is_only_kept_when_a_deeper_search_confirms_it() -> None:
    start, plies = svc.walk_pgn(HANGING_QUEEN)
    target = plies[6]
    lines = _quiet(start, plies)
    # the engine ranked the played move itself and scores it far below its own best move
    lines[target.fen_before] = [
        _line(1, "f1e1", "Re1", cp=20),
        _line(2, "d1e2", "Qe2", cp=-400),
    ]

    _, shallow = svc.build_analysis(start, plies, lines)
    assert shallow[6].classification == "blunder"

    asked: list[tuple[str, str]] = []

    def confirm(before: str, after: str) -> tuple[schemas.Score, schemas.Score]:
        asked.append((before, after))
        return schemas.Score(cp=880), schemas.Score(cp=900)

    summary, moves = svc.build_analysis(start, plies, lines, confirm)
    # only the suspect ply costs a second search
    assert asked == [(target.fen_before, target.fen_after)]
    assert moves[6].classification == "best" and moves[6].win_prob_loss == 0.0
    assert (moves[6].eval_before, moves[6].eval_after) == (
        schemas.Score(cp=880),
        schemas.Score(cp=900),
    )
    # the chart follows the numbers the verdict was made from
    assert summary.eval_series[7] == 9.0


def test_the_engines_own_move_is_not_best_when_the_next_search_finds_mate() -> None:
    """`ply.uci == best_uci` used to zero the loss outright, so a move the engine recommended
    and that walks into a forced mate was reported as best with 100 accuracy."""
    start, plies = svc.walk_pgn(HANGING_QUEEN)
    target = plies[10]  # 6.fxe3, the last ply
    assert (target.san, target.uci, target.color) == ("fxe3", "f2e3", "white")

    lines = _quiet(start, plies)
    lines[target.fen_before] = [_line(1, "f2e3", "fxe3", cp=20)]  # the played move is rank 1
    lines[target.fen_after] = [_line(1, "d8h4", "Qh4", mate=-2)]  # one ply on, Black mates
    calls: list[tuple[str, str]] = []

    def confirm(before: str, after: str) -> tuple[schemas.Score, schemas.Score]:
        calls.append((before, after))
        return schemas.Score(cp=25), schemas.Score(mate=-2)

    _, moves = svc.build_analysis(start, plies, lines, confirm)
    assert calls == [(target.fen_before, target.fen_after)]
    assert moves[10].best_move_uci == target.uci
    assert moves[10].classification == "blunder" and moves[10].win_prob_loss > 0.2
    assert svc.move_accuracy(moves[10].win_prob_loss) < 90.0

    # but when the deeper search says the mate was already there, the move is not to blame
    def lost_already(before: str, after: str) -> tuple[schemas.Score, schemas.Score]:
        return schemas.Score(mate=-3), schemas.Score(mate=-2)

    _, moves = svc.build_analysis(start, plies, lines, lost_already)
    assert moves[10].classification == "best"


def test_confirmation_is_skipped_when_both_readings_agree_the_move_is_fine() -> None:
    start, plies = svc.walk_pgn(HANGING_QUEEN)
    lines = _quiet(start, plies)

    def confirm(before: str, after: str) -> tuple[schemas.Score, schemas.Score]:
        raise AssertionError("a quiet game must not pay for confirmation searches")

    _, moves = svc.build_analysis(start, plies, lines, confirm)
    assert {m.classification for m in moves} == {"book", "best"}


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


async def test_position_endpoint_rejects_an_illegal_position(aclient: AsyncClient) -> None:
    """Two white kings and no black king is a legal FEN string and an illegal position.
    Stockfish segfaults on it, so it must be refused before the engine is started."""
    res = await aclient.post("/analysis/position", json={"fen": "8/8/8/8/8/8/8/KKQ5 w - - 0 1"})
    assert res.status_code == 422
    assert "규칙" in res.json()["detail"]


async def test_position_endpoint_does_not_blame_a_missing_binary_for_a_dead_engine(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EngineTerminatedError is a RuntimeError, so a killed process used to be reported as
    'Stockfish is not installed'."""

    async def killed(*_args: object, **_kwargs: object) -> list[schemas.EngineLine]:
        raise chess.engine.EngineTerminatedError("engine process died unexpectedly")

    monkeypatch.setattr(svc, "get_lines", killed)
    res = await aclient.post("/analysis/position", json={"fen": chess.STARTING_FEN})
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert detail == analysis_router.ENGINE_DIED and detail != analysis_router.NO_ENGINE


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


# ---------- cache identity, pool, job plumbing ----------


def test_cache_name_carries_the_options_that_change_a_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threads and Hash are not part of the position but they change the lines a search returns,
    so two configurations must not read each other's rows."""
    monkeypatch.setattr(svc, "get_settings", lambda: Settings(engine_threads=1, engine_hash_mb=16))
    assert svc.cache_name("stockfish-18") == "stockfish-18-t1-h16"
    monkeypatch.setattr(svc, "get_settings", lambda: Settings(engine_threads=8, engine_hash_mb=512))
    assert svc.cache_name("stockfish-18") == "stockfish-18-t8-h512"


def test_pool_refuses_to_queue_forever_and_closes_borrowed_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Fake:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(engine_mod, "Engine", Fake)
    pool = engine_mod.EnginePool(size=1, wait=0.01)
    with pool.borrow() as first:
        with pytest.raises(engine_mod.EngineBusy) as caught:
            with pool.borrow():
                pass  # pragma: no cover - the borrow above raises
        assert "엔진" in str(caught.value)
        # an engine checked out at shutdown is quit too, or its process outlives the interpreter
        pool.close()
    assert first.closed


async def test_job_wait_reports_a_timeout_instead_of_a_running_job() -> None:
    jobs = JobRunner()

    async def slow() -> None:
        await asyncio.sleep(5.0)

    jobs.submit("slow", slow)
    with pytest.raises(TimeoutError):
        await jobs.wait("slow", timeout=0.1)
    await jobs.stop()


async def test_get_or_analyze_waits_instead_of_starting_a_second_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = await _insert_game(SCHOLARS_MATE, "in-flight")
    async with db.session_factory()() as session:
        await svc.reset_analysis(session, game_id, 8, "stockfish-18", status="running")
        await session.commit()

    async def never(key: str, timeout: float = 60.0) -> Job:
        raise TimeoutError(key)

    def boom(*_: object, **__: object) -> None:
        raise AssertionError("a second analysis of the same game must not start")

    monkeypatch.setattr(runner, "get", lambda key: Job(key=key, fn=never, status="running"))
    monkeypatch.setattr(runner, "wait", never)
    monkeypatch.setattr(svc, "run_analysis", boom)

    got = await svc.get_or_analyze(game_id)
    assert got.status == "running" and got.moves == []


@needs_engine
async def test_a_stored_analysis_only_answers_the_depth_it_was_computed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = await _insert_game(SCHOLARS_MATE, "depth-param")
    first = await svc.get_or_analyze(game_id, depth=4, multipv=1)
    assert first.status == "done" and first.depth == 4 and first.summary.multipv == 1

    # asking for nothing in particular takes what is stored, without the engine
    def boom(*_: object, **__: object) -> None:
        raise AssertionError("engine must not run for a request the stored analysis answers")

    monkeypatch.setattr(svc, "_analyze", boom)
    assert (await svc.get_or_analyze(game_id)).depth == 4
    assert (await svc.get_or_analyze(game_id, depth=4, multipv=1)).depth == 4
    monkeypatch.undo()

    # asking for a different depth or width re-runs rather than silently serving depth 4
    deeper = await svc.get_or_analyze(game_id, depth=6, multipv=2)
    assert deeper.depth == 6 and deeper.summary.multipv == 2
    assert (await svc.get_analysis(game_id)).depth == 6


@needs_engine
def test_a_line_does_not_depend_on_the_positions_analysed_before_it() -> None:
    """EngineCache is keyed by (fen, engine, depth, multipv) alone, so a batch must not leave its
    transposition table behind for the next position."""
    start, plies = svc.walk_pgn(HANGING_QUEEN)
    boards = [chess.Board(fen) for fen in _fens(HANGING_QUEEN)]
    last = boards[-1]
    alone = svc.analyse_board(last.copy(), depth=8, multipv=2)
    in_batch = svc.analyse_boards(boards, depth=8, multipv=2)[-1]
    assert in_batch == alone
    assert len(plies) == len(boards) - 1 and start == boards[0]


# 15.Bxd7+ starts a forced mate in three: 15...Nxd7 16.Qb8+ Nxb8 17.Rd8#. At depth 12 a search of
# the position after it is blind to the mate and reports +2.5 against the parent's +5.8, which
# used to be billed to Morphy as a blunder.
OPERA_FINISH = (
    '[White "Morphy"]\n[Black "Duke"]\n[Result "1-0"]\n'
    '[FEN "4kb1r/p2r1ppp/4qn2/1B2p1B1/4P3/1Q6/PPP2PPP/2KR4 w k - 2 15"]\n[SetUp "1"]\n\n'
    "15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0"
)


@needs_engine
def test_the_start_of_a_forced_mate_is_not_a_blunder() -> None:
    game = models.Game(pgn=OPERA_FINISH, source="pgn", white="Morphy", black="Duke", result="1-0")
    summary, moves = svc.analyze_game(game, depth=12, multipv=3)

    assert [m.san for m in moves] == ["Bxd7+", "Nxd7", "Qb8+", "Nxb8", "Rd8#"]
    sacrifice = moves[0]
    assert sacrifice.classification in ("best", "good"), sacrifice
    assert sacrifice.win_prob_loss < 0.02
    # the confirmation search sees the mate, so White is already winning before the sacrifice
    assert sacrifice.eval_before.as_pawns() > 5.0
    # and Black's only reply is not credited as a perfect move
    assert moves[1].color == "black" and moves[1].classification != "best"
    assert summary.counts["white"]["blunder"] == 0
    assert summary.accuracy_white > summary.accuracy_black
