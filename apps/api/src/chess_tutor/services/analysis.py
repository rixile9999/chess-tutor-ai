"""Engine analysis pipeline (layer 1) plus move classification.

Everything here is deterministic given the engine output: win probabilities, classifications
and accuracies are arithmetic on the lines the engine returned, so every number in a
`MoveAnalysis` can be traced back to a stored `EngineLine`.

Sync functions (`analyse_position`, `analyze_game`) run the engine directly and are meant for
worker threads. Async functions (`get_lines`, `run_analysis`, `get_or_analyze`) add the database
cache and the job plumbing.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import chess
import chess.pgn
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models, schemas
from chess_tutor.config import get_settings
from chess_tutor.db import session_factory
from chess_tutor.engine import Engine, EngineBusy, Line, pool
from chess_tutor.jobs import runner
from chess_tutor.openings import classify_game, lookup

log = logging.getLogger(__name__)

CLASSIFICATIONS: tuple[schemas.Classification, ...] = (
    "book",
    "best",
    "good",
    "inaccuracy",
    "mistake",
    "blunder",
    "forced",
)

MATE_CP = 10_000
"""Centipawn stand-in for a position where mate has already been delivered. `Score.mate` cannot
carry the sign of a 0-move mate, and +-10000 cp behaves like a mate in every consumer
(`Score.as_pawns` gives +-100, win probability rounds to 1 or 0)."""

WIN_PROB_K = 0.00368208
"""Slope of the centipawn -> win probability logistic (the lichess constant)."""

EVAL_CLAMP = 10.0
"""eval_series is clamped to +-10 pawns so the chart stays readable around forced mates."""

MATE_STEP_CP = 100
"""Centipawns a mate loses per move of distance when it is turned into a score, the convention
services/maia.py already uses. Mate in 1 therefore outscores mate in 12, and a delivered mate
(MATE_CP) outscores every announced mate."""

CONFIRM_DEPTH_BONUS = 6
"""Extra plies for the confirmation search of a move that looks like a mistake or a blunder.
`eval_before` and `eval_after` come from two different searches, so at the base depth a move
that opens a forced win one ply beyond the horizon reads as a loss. Re-searching both positions
deeper resolves it: on 15.Bxd7+ of the Opera game the depth-12 loss is 0.170 (blunder), +4 still
gives 0.112 (mistake) and +6 gives 0.021 (inaccuracy), matching the depth-20 verdict."""

# win-probability loss thresholds, checked in order
_BEST = 0.005
_GOOD = 0.02
_INACCURACY = 0.06
_MISTAKE = 0.15

_SUSPECT: tuple[schemas.Classification, ...] = ("mistake", "blunder")
"""Labels harsh enough to be worth a deeper look before they are reported."""


def job_key(game_id: int) -> str:
    return f"analysis:{game_id}"


# ---------- pure arithmetic ----------


def mate_cp(mate: int) -> int:
    """Centipawn stand-in for `mate` moves away, from White's point of view. Distance is kept so
    that a shorter mate scores higher; the value stays far enough out that win_prob is 1 or 0 to
    within 1e-8, which is what every consumer of a mate score expects."""
    distance = min(abs(mate), 50) * MATE_STEP_CP
    return (MATE_CP - distance) * (1 if mate > 0 else -1)


def win_prob(score: schemas.Score, color: schemas.Color = "white") -> float:
    """Probability (0..1) that `color` wins, from a score given from White's point of view."""
    cp = mate_cp(score.mate) if score.mate is not None else (score.cp or 0)
    cp = max(-MATE_CP, min(MATE_CP, cp))
    if color == "black":
        cp = -cp
    return 1.0 / (1.0 + math.exp(-WIN_PROB_K * cp))


def win_prob_loss(before: schemas.Score, after: schemas.Score, color: schemas.Color) -> float:
    """Drop in the mover's win probability from the position before the move to the value of the
    move that was played. Never negative: small depth artefacts should not turn into a bonus.

    Both scores must come from the same search effort. `build_analysis` takes `after` from the
    parent's own MultiPV line for the played move when the engine returned one, and confirms a
    harsh verdict with a deeper search of both positions, because a score for the child position
    searched to the same depth is effectively one ply deeper than the parent's and their
    difference is not a property of the move."""
    return max(0.0, win_prob(before, color) - win_prob(after, color))


def classify_loss(loss: float) -> schemas.Classification:
    if loss < _BEST:
        return "best"
    if loss < _GOOD:
        return "good"
    if loss < _INACCURACY:
        return "inaccuracy"
    if loss < _MISTAKE:
        return "mistake"
    return "blunder"


def move_accuracy(loss: float) -> float:
    """Per-move accuracy (0..100) from win-probability loss, the lichess curve."""
    raw = 103.1668 * math.exp(-0.04354 * (loss * 100.0)) - 3.1669
    return max(0.0, min(100.0, raw))


def clamp_pawns(score: schemas.Score) -> float:
    return max(-EVAL_CLAMP, min(EVAL_CLAMP, score.as_pawns()))


def terminal_score(board: chess.Board) -> schemas.Score | None:
    """Score of a position with no legal continuation, or None when the game goes on."""
    if board.is_checkmate():
        return schemas.Score(cp=-MATE_CP if board.turn == chess.WHITE else MATE_CP)
    if board.is_game_over():
        return schemas.Score(cp=0)
    return None


# ---------- engine lines ----------


def _to_engine_line(board: chess.Board, line: Line) -> schemas.EngineLine:
    sans: list[str] = []
    ucis: list[str] = []
    probe = board.copy()
    for move in line.pv:
        if not probe.is_legal(move):
            break
        sans.append(probe.san(move))
        ucis.append(move.uci())
        probe.push(move)
    return schemas.EngineLine(
        rank=line.rank,
        score=schemas.Score(cp=line.score_cp, mate=line.mate),
        pv=sans,
        pv_uci=ucis,
    )


def analyse_board(
    board: chess.Board,
    depth: int,
    multipv: int,
    engine: Engine | None = None,
) -> list[schemas.EngineLine]:
    """Top lines for one position, White's point of view. Uses the shared pool when no engine is
    given. A finished position yields one line with an empty pv."""
    terminal = terminal_score(board)
    if terminal is not None:
        return [schemas.EngineLine(rank=1, score=terminal, pv=[], pv_uci=[])]
    if engine is None:
        with pool.borrow() as borrowed:
            raw = borrowed.analyse(board, depth=depth, multipv=multipv)
    else:
        raw = engine.analyse(board, depth=depth, multipv=multipv)
    lines = [_to_engine_line(board, line) for line in raw]
    if not lines:
        raise RuntimeError(f"engine returned no lines for {board.fen()}")
    return lines


def analyse_position(
    fen: str, depth: int | None = None, multipv: int | None = None, engine: Engine | None = None
) -> list[schemas.EngineLine]:
    """Sync: analyse one FEN. Raises ValueError for an invalid FEN."""
    settings = get_settings()
    board = chess.Board(fen)
    return analyse_board(
        board, depth or settings.engine_depth, multipv or settings.engine_multipv, engine
    )


def analyse_boards(
    boards: Iterable[chess.Board], depth: int, multipv: int, engine: Engine | None = None
) -> list[list[schemas.EngineLine]]:
    """Analyse many positions (one game) with a single engine process. Each search still starts
    from a cleared table (Engine.analyse), so a line does not depend on the positions analysed
    before it and may be cached under (fen, engine, depth, multipv) alone."""
    boards = list(boards)
    if engine is None:
        with pool.borrow() as borrowed:
            return [analyse_board(b, depth, multipv, borrowed) for b in boards]
    return [analyse_board(b, depth, multipv, engine) for b in boards]


def cache_name(name: str) -> str:
    """Cache identity of an engine: its UCI name plus the options that change the search. Threads
    and Hash are not part of the position, but they change the lines a search returns, so two
    configurations must not read each other's cached rows."""
    settings = get_settings()
    return f"{name}-t{settings.engine_threads}-h{settings.engine_hash_mb}"


def engine_name() -> str:
    """Name used as the cache key, e.g. 'stockfish-18-t1-h256'. Starts an engine the first time."""
    global _engine_name
    if _engine_name is None:
        with pool.borrow() as engine:
            _engine_name = cache_name(engine.name)
    return _engine_name


_engine_name: str | None = None


# ---------- cache ----------


async def _cached_lines(
    session: AsyncSession, fens: Iterable[str], engine: str, depth: int, multipv: int
) -> dict[str, list[schemas.EngineLine]]:
    fens = list(dict.fromkeys(fens))
    if not fens:
        return {}
    found: dict[str, list[schemas.EngineLine]] = {}
    # SQLite limits the number of bound parameters; chunk long games.
    for i in range(0, len(fens), 500):
        stmt = select(models.EngineCache).where(
            models.EngineCache.fen.in_(fens[i : i + 500]),
            models.EngineCache.engine == engine,
            models.EngineCache.depth == depth,
            models.EngineCache.multipv == multipv,
        )
        for row in (await session.execute(stmt)).scalars():
            found[row.fen] = [schemas.EngineLine.model_validate(item) for item in row.lines]
    return found


async def _store_lines(
    session: AsyncSession,
    entries: Mapping[str, list[schemas.EngineLine]],
    engine: str,
    depth: int,
    multipv: int,
) -> None:
    """Insert new cache rows one by one so a concurrent writer of the same position only
    costs us that row, not the whole batch."""
    for fen, lines in entries.items():
        session.add(
            models.EngineCache(
                fen=fen,
                engine=engine,
                depth=depth,
                multipv=multipv,
                lines=[line.model_dump(mode="json") for line in lines],
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


async def get_lines(
    fen: str, depth: int | None = None, multipv: int | None = None
) -> list[schemas.EngineLine]:
    """Top lines for a FEN, served from EngineCache when present and stored after computing."""
    settings = get_settings()
    depth = depth or settings.engine_depth
    multipv = multipv or settings.engine_multipv
    board = chess.Board(fen)
    fen = board.fen()
    name = await asyncio.to_thread(engine_name)
    async with session_factory()() as session:
        cached = await _cached_lines(session, [fen], name, depth, multipv)
        if fen in cached:
            return cached[fen]
    lines = await asyncio.to_thread(analyse_position, fen, depth, multipv)
    async with session_factory()() as session:
        await _store_lines(session, {fen: lines}, name, depth, multipv)
    return lines


# ---------- game walk ----------


@dataclass(frozen=True)
class Ply:
    index: int
    """1-based ply number."""
    move: chess.Move
    san: str
    uci: str
    color: schemas.Color
    fen_before: str
    fen_after: str
    legal_moves: int
    clock: float | None


def walk_pgn(pgn: str) -> tuple[chess.Board, list[Ply]]:
    """Start position and every mainline ply. Raises ValueError when the PGN cannot be read or
    contains an illegal move: a silently truncated game would produce wrong statistics."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("PGN을 읽을 수 없습니다")
    if game.errors:
        raise ValueError(f"PGN에 둘 수 없는 수가 있습니다: {game.errors[0]}")
    start = game.board()
    board = start.copy()
    plies: list[Ply] = []
    for node in game.mainline():
        move = node.move
        if move is None:
            continue
        san = board.san(move)
        fen_before = board.fen()
        color: schemas.Color = "white" if board.turn == chess.WHITE else "black"
        legal = board.legal_moves.count()
        board.push(move)
        plies.append(
            Ply(
                index=len(plies) + 1,
                move=move,
                san=san,
                uci=move.uci(),
                color=color,
                fen_before=fen_before,
                fen_after=board.fen(),
                legal_moves=legal,
                clock=node.clock(),
            )
        )
    return start, plies


def _book_flags(start: chess.Board, plies: list[Ply]) -> list[bool]:
    """A ply is a book move while the game is still following a named opening and the position
    it produces is itself in the book (so the first deviation is judged by the engine)."""
    _, left_at = classify_game([p.move for p in plies], start)
    flags: list[bool] = []
    board = start.copy()
    for i, ply in enumerate(plies):
        board.push(ply.move)
        flags.append(i < left_at and lookup(board) is not None)
    return flags


# ---------- game analysis ----------

Confirm = Callable[[str, str], tuple[schemas.Score, schemas.Score] | None]
"""Deeper second opinion on one ply: given (fen_before, fen_after) it returns the two scores of
a deeper search of both positions, or None when it cannot run."""


def played_line(lines: Iterable[schemas.EngineLine], uci: str) -> schemas.EngineLine | None:
    """The line among the position's own MultiPV lines that starts with `uci`, if the engine
    returned one. Its score is what the same search thought the played move was worth, so it can
    be compared with the position's score without mixing depths."""
    for line in lines:
        if line.pv_uci and line.pv_uci[0] == uci:
            return line
    return None


def build_analysis(
    start: chess.Board,
    plies: list[Ply],
    lines_by_fen: Mapping[str, list[schemas.EngineLine]],
    confirm: Confirm | None = None,
) -> tuple[schemas.AnalysisSummary, list[schemas.MoveAnalysis]]:
    """Classify every ply from engine lines only. `lines_by_fen` must cover the start position
    and the position after every ply.

    The value of the played move comes from the position's own MultiPV line for it when the
    engine returned one, and from the search of the resulting position otherwise. A verdict of
    mistake or blunder from either number is put to `confirm`, a deeper search of both
    positions, and the deeper answer is the one reported: at a fixed depth the two searches sit
    one ply apart and a combination that wins just past the horizon reads as a loss."""
    book = _book_flags(start, plies)
    moves: list[schemas.MoveAnalysis] = []
    eval_series = [clamp_pawns(lines_by_fen[start.fen()][0].score)]
    accuracies: dict[str, list[float]] = {"white": [], "black": []}
    counts: dict[str, dict[str, int]] = {
        color: dict.fromkeys(CLASSIFICATIONS, 0) for color in ("white", "black")
    }
    for i, ply in enumerate(plies):
        lines = lines_by_fen[ply.fen_before]
        eval_before = lines[0].score
        child = lines_by_fen[ply.fen_after][0].score
        line = played_line(lines, ply.uci)
        eval_after = line.score if line is not None else child
        best_san = lines[0].pv[0] if lines[0].pv else None
        best_uci = lines[0].pv_uci[0] if lines[0].pv_uci else None
        loss = win_prob_loss(eval_before, eval_after, ply.color)
        suspect = max(loss, win_prob_loss(eval_before, child, ply.color))
        if confirm is not None and classify_loss(suspect) in _SUSPECT:
            deeper = confirm(ply.fen_before, ply.fen_after)
            if deeper is not None:
                eval_before, eval_after = deeper
                loss = win_prob_loss(eval_before, eval_after, ply.color)
        eval_series.append(clamp_pawns(eval_after))
        classification: schemas.Classification
        if book[i]:
            classification = "book"
        elif ply.legal_moves == 1:
            classification = "forced"
        else:
            classification = classify_loss(loss)
        counts[ply.color][classification] += 1
        accuracies[ply.color].append(move_accuracy(loss))
        moves.append(
            schemas.MoveAnalysis(
                ply=ply.index,
                san=ply.san,
                uci=ply.uci,
                color=ply.color,
                fen_before=ply.fen_before,
                fen_after=ply.fen_after,
                eval_before=eval_before,
                eval_after=eval_after,
                best_move_san=best_san,
                best_move_uci=best_uci,
                classification=classification,
                win_prob_loss=round(loss, 6),
                lines=list(lines),
                clock=ply.clock,
            )
        )

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    summary = schemas.AnalysisSummary(
        accuracy_white=mean(accuracies["white"]),
        accuracy_black=mean(accuracies["black"]),
        counts=counts,
        eval_series=eval_series,
    )
    return summary, moves


Cached = Mapping[tuple[int, int], Mapping[str, list[schemas.EngineLine]]]
"""Engine lines already known, grouped by the (depth, multipv) they were computed at."""

Fresh = dict[tuple[int, int], dict[str, list[schemas.EngineLine]]]


def _confirmer(depth: int, multipv: int, engine: Engine, known: Cached, fresh: Fresh) -> Confirm:
    """Second opinion on one ply at `depth`, single line. Results land in `fresh` under their own
    (depth, multipv) key so they are cached like any other line and a re-analysis reuses them."""
    key = (depth, multipv)
    have = dict(known.get(key, {}))
    new = fresh.setdefault(key, {})

    def score(fen: str) -> schemas.Score:
        lines = have.get(fen) or new.get(fen)
        if lines is None:
            lines = analyse_board(chess.Board(fen), depth, multipv, engine)
            new[fen] = lines
        return lines[0].score

    def confirm(fen_before: str, fen_after: str) -> tuple[schemas.Score, schemas.Score]:
        return score(fen_before), score(fen_after)

    return confirm


def _analyze_with(
    engine: Engine, pgn: str, depth: int, multipv: int, known: Cached
) -> tuple[schemas.AnalysisSummary, list[schemas.MoveAnalysis], Fresh]:
    start, plies = walk_pgn(pgn)
    fens = [start.fen()] + [p.fen_after for p in plies]
    lines_by_fen: dict[str, list[schemas.EngineLine]] = dict(known.get((depth, multipv), {}))
    missing = [fen for fen in dict.fromkeys(fens) if fen not in lines_by_fen]
    fresh: Fresh = {}
    if missing:
        results = analyse_boards((chess.Board(fen) for fen in missing), depth, multipv, engine)
        computed = dict(zip(missing, results, strict=True))
        fresh[(depth, multipv)] = computed
        lines_by_fen.update(computed)
    confirm = _confirmer(depth + CONFIRM_DEPTH_BONUS, 1, engine, known, fresh)
    summary, moves = build_analysis(start, plies, lines_by_fen, confirm)
    summary.multipv = multipv
    return summary, moves, fresh


def _analyze(
    pgn: str,
    depth: int,
    multipv: int,
    engine: Engine | None,
    known: Cached | None,
) -> tuple[schemas.AnalysisSummary, list[schemas.MoveAnalysis], Fresh]:
    """Walk the game, analyse the positions not in `known`, classify. The third value holds the
    lines that were freshly computed, for the caller to cache. One engine serves the whole game,
    including the deeper confirmation searches."""
    if engine is not None:
        return _analyze_with(engine, pgn, depth, multipv, known or {})
    with pool.borrow() as borrowed:
        return _analyze_with(borrowed, pgn, depth, multipv, known or {})


def analyze_game(
    game: models.Game,
    depth: int | None = None,
    multipv: int | None = None,
    engine: Engine | None = None,
    known: Mapping[str, list[schemas.EngineLine]] | None = None,
) -> tuple[schemas.AnalysisSummary, list[schemas.MoveAnalysis]]:
    """Sync, engine only: analyse every position of the game and classify each move.
    `known` lets a caller pass lines it already has (the cache) at `depth`/`multipv` so they are
    not recomputed."""
    settings = get_settings()
    depth = depth or settings.engine_depth
    multipv = multipv or settings.engine_multipv
    summary, moves, _ = _analyze(
        game.pgn, depth, multipv, engine, {(depth, multipv): known} if known else None
    )
    return summary, moves


# ---------- persistence and jobs ----------


def to_game_analysis(row: models.Analysis) -> schemas.GameAnalysis:
    status: schemas.AnalysisStatus = row.status  # type: ignore[assignment]
    return schemas.GameAnalysis(
        game_id=row.game_id,
        status=status,
        engine=row.engine,
        depth=row.depth,
        error=row.error,
        summary=schemas.AnalysisSummary.model_validate(row.summary or {}),
        moves=[schemas.MoveAnalysis.model_validate(m) for m in row.moves or []],
    )


async def get_analysis_row(session: AsyncSession, game_id: int) -> models.Analysis | None:
    stmt = select(models.Analysis).where(models.Analysis.game_id == game_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_analysis(game_id: int) -> schemas.GameAnalysis:
    """Current stored state; status 'none' when the game was never analysed."""
    async with session_factory()() as session:
        row = await get_analysis_row(session, game_id)
        if row is None:
            return schemas.GameAnalysis(game_id=game_id, status="none")
        return to_game_analysis(row)


async def reset_analysis(
    session: AsyncSession, game_id: int, depth: int, engine: str, status: str = "pending"
) -> models.Analysis:
    """Create the Analysis row for a game or wipe the existing one. Does not commit."""
    row = await get_analysis_row(session, game_id)
    if row is None:
        row = models.Analysis(game_id=game_id)
        session.add(row)
    row.engine = engine
    row.depth = depth
    row.status = status
    row.error = None
    row.summary = {}
    row.moves = []
    return row


async def run_analysis(
    game_id: int, depth: int | None = None, multipv: int | None = None
) -> schemas.GameAnalysis:
    """Analyse a stored game end to end. Marks the row running, computes in a worker thread,
    then stores the result as done, or failed with the error text. Never raises for engine or
    PGN problems: those end up in the row."""
    settings = get_settings()
    depth = depth or settings.engine_depth
    multipv = multipv or settings.engine_multipv
    factory = session_factory()

    async with factory() as session:
        game = await session.get(models.Game, game_id)
        if game is None:
            raise ValueError(f"game {game_id} not found")
        pgn = game.pgn
        try:
            name = await asyncio.to_thread(engine_name)
        except RuntimeError as exc:
            row = await reset_analysis(session, game_id, depth, "stockfish", status="failed")
            missing = f"엔진을 찾을 수 없습니다: {exc}"
            row.error = str(exc) if isinstance(exc, EngineBusy) else missing
            await session.commit()
            return to_game_analysis(row)
        row = await reset_analysis(session, game_id, depth, name, status="running")
        await session.commit()

    try:
        start, plies = walk_pgn(pgn)
        fens = [start.fen()] + [p.fen_after for p in plies]
        keys = [(depth, multipv), (depth + CONFIRM_DEPTH_BONUS, 1)]
        async with factory() as session:
            known: Fresh = {key: await _cached_lines(session, fens, name, *key) for key in keys}
        summary, moves, fresh = await asyncio.to_thread(_analyze, pgn, depth, multipv, None, known)
        async with factory() as session:
            for (fresh_depth, fresh_multipv), entries in fresh.items():
                await _store_lines(session, entries, name, fresh_depth, fresh_multipv)
    except Exception as exc:  # noqa: BLE001 - recorded on the row, the job must not die silently
        log.exception("analysis of game %s failed", game_id)
        async with factory() as session:
            row = await reset_analysis(session, game_id, depth, name, status="failed")
            row.error = str(exc)
            await session.commit()
            return to_game_analysis(row)

    async with factory() as session:
        row = await reset_analysis(session, game_id, depth, name, status="done")
        row.summary = summary.model_dump(mode="json")
        row.moves = [m.model_dump(mode="json") for m in moves]
        await session.commit()
        return to_game_analysis(row)


def submit_analysis(game_id: int, depth: int | None = None, multipv: int | None = None) -> str:
    """Queue run_analysis on the shared runner; returns the job key."""
    key = job_key(game_id)

    async def job() -> None:
        await run_analysis(game_id, depth, multipv)

    runner.submit(key, job)
    return key


def answers(row: models.Analysis, depth: int | None, multipv: int | None) -> bool:
    """Whether a finished row answers this request. A caller that asked for a depth or a MultiPV
    width gets exactly that or a new run: the parameter is documented on /review and /analysis,
    and silently serving a shallower analysis is what made it look like it did nothing. A caller
    that asked for neither takes whatever is stored."""
    if depth is not None and row.depth != depth:
        return False
    stored = (row.summary or {}).get("multipv", 0)
    return not (multipv is not None and stored != multipv)


async def _stored(
    game_id: int, depth: int | None, multipv: int | None
) -> schemas.GameAnalysis | None:
    async with session_factory()() as session:
        row = await get_analysis_row(session, game_id)
        if row is not None and row.status == "done" and answers(row, depth, multipv):
            return to_game_analysis(row)
    return None


async def get_or_analyze(
    game_id: int, depth: int | None = None, multipv: int | None = None
) -> schemas.GameAnalysis:
    """Stored analysis when it is done and was computed for this depth and width; otherwise
    analyse now and return the result. Other modules (review, profile, puzzles) call this and can
    rely on getting a finished analysis, a row whose status is 'failed' with an error, or a
    'running' row when another caller's analysis of the same game is still going."""
    stored = await _stored(game_id, depth, multipv)
    if stored is not None:
        return stored
    key = job_key(game_id)
    job = runner.get(key)
    if job is not None and job.status in ("pending", "running"):
        try:
            await runner.wait(key, timeout=600.0)
        except TimeoutError:
            # Still running: report that rather than starting a second analysis of the same game.
            return await get_analysis(game_id)
        stored = await _stored(game_id, depth, multipv)
        if stored is not None:
            return stored
    return await run_analysis(game_id, depth, multipv)
