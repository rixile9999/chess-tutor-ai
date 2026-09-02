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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import chess
import chess.pgn
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models, schemas
from chess_tutor.config import get_settings
from chess_tutor.db import session_factory
from chess_tutor.engine import Engine, Line, pool
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

# win-probability loss thresholds, checked in order
_BEST = 0.005
_GOOD = 0.02
_INACCURACY = 0.06
_MISTAKE = 0.15


def job_key(game_id: int) -> str:
    return f"analysis:{game_id}"


# ---------- pure arithmetic ----------


def win_prob(score: schemas.Score, color: schemas.Color = "white") -> float:
    """Probability (0..1) that `color` wins, from a score given from White's point of view."""
    if score.mate is not None:
        white_wins = score.mate > 0
        return 1.0 if white_wins == (color == "white") else 0.0
    cp = score.cp or 0
    if color == "black":
        cp = -cp
    return 1.0 / (1.0 + math.exp(-WIN_PROB_K * cp))


def win_prob_loss(before: schemas.Score, after: schemas.Score, color: schemas.Color) -> float:
    """Drop in the mover's win probability from the best line before the move to the best line
    after it. Never negative: a move cannot do better than the engine's own best line, and
    small depth artefacts should not turn into a bonus."""
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
    game: object | None = None,
) -> list[schemas.EngineLine]:
    """Top lines for one position, White's point of view. Uses the shared pool when no engine is
    given. A finished position yields one line with an empty pv. `game` lets the positions of one
    game share the engine's transposition table (see Engine.analyse)."""
    terminal = terminal_score(board)
    if terminal is not None:
        return [schemas.EngineLine(rank=1, score=terminal, pv=[], pv_uci=[])]
    if engine is None:
        with pool.borrow() as borrowed:
            raw = borrowed.analyse(board, depth=depth, multipv=multipv, game=game)
    else:
        raw = engine.analyse(board, depth=depth, multipv=multipv, game=game)
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
    """Analyse many positions (one game) with a single engine process and one table."""
    boards = list(boards)
    game = object()
    if engine is None:
        with pool.borrow() as borrowed:
            return [analyse_board(b, depth, multipv, borrowed, game) for b in boards]
    return [analyse_board(b, depth, multipv, engine, game) for b in boards]


def engine_name() -> str:
    """Name used as the cache key, e.g. 'stockfish-18'. Starts an engine the first time."""
    global _engine_name
    if _engine_name is None:
        with pool.borrow() as engine:
            _engine_name = engine.name
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


def build_analysis(
    start: chess.Board,
    plies: list[Ply],
    lines_by_fen: Mapping[str, list[schemas.EngineLine]],
) -> tuple[schemas.AnalysisSummary, list[schemas.MoveAnalysis]]:
    """Classify every ply from engine lines only. `lines_by_fen` must cover the start position
    and the position after every ply."""
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
        eval_after = lines_by_fen[ply.fen_after][0].score
        eval_series.append(clamp_pawns(eval_after))
        best_san = lines[0].pv[0] if lines[0].pv else None
        best_uci = lines[0].pv_uci[0] if lines[0].pv_uci else None
        loss = win_prob_loss(eval_before, eval_after, ply.color)
        if ply.uci == best_uci:
            loss = 0.0
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


def _analyze(
    pgn: str,
    depth: int,
    multipv: int,
    engine: Engine | None,
    known: Mapping[str, list[schemas.EngineLine]] | None,
) -> tuple[
    schemas.AnalysisSummary, list[schemas.MoveAnalysis], dict[str, list[schemas.EngineLine]]
]:
    """Walk the game, analyse the positions not in `known`, classify. The third value holds the
    lines that were freshly computed, for the caller to cache."""
    start, plies = walk_pgn(pgn)
    fens = [start.fen()] + [p.fen_after for p in plies]
    lines_by_fen: dict[str, list[schemas.EngineLine]] = dict(known or {})
    missing = [fen for fen in dict.fromkeys(fens) if fen not in lines_by_fen]
    fresh: dict[str, list[schemas.EngineLine]] = {}
    if missing:
        results = analyse_boards((chess.Board(fen) for fen in missing), depth, multipv, engine)
        fresh = dict(zip(missing, results, strict=True))
        lines_by_fen.update(fresh)
    summary, moves = build_analysis(start, plies, lines_by_fen)
    return summary, moves, fresh


def analyze_game(
    game: models.Game,
    depth: int | None = None,
    multipv: int | None = None,
    engine: Engine | None = None,
    known: Mapping[str, list[schemas.EngineLine]] | None = None,
) -> tuple[schemas.AnalysisSummary, list[schemas.MoveAnalysis]]:
    """Sync, engine only: analyse every position of the game and classify each move.
    `known` lets a caller pass lines it already has (the cache) so they are not recomputed."""
    settings = get_settings()
    summary, moves, _ = _analyze(
        game.pgn,
        depth or settings.engine_depth,
        multipv or settings.engine_multipv,
        engine,
        known,
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
            row.error = f"엔진을 찾을 수 없습니다: {exc}"
            await session.commit()
            return to_game_analysis(row)
        row = await reset_analysis(session, game_id, depth, name, status="running")
        await session.commit()

    try:
        start, plies = walk_pgn(pgn)
        fens = [start.fen()] + [p.fen_after for p in plies]
        async with factory() as session:
            known = await _cached_lines(session, fens, name, depth, multipv)
        summary, moves, fresh = await asyncio.to_thread(_analyze, pgn, depth, multipv, None, known)
        async with factory() as session:
            await _store_lines(session, fresh, name, depth, multipv)
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


async def get_or_analyze(
    game_id: int, depth: int | None = None, multipv: int | None = None
) -> schemas.GameAnalysis:
    """Stored analysis when it is done; otherwise analyse now and return the result. Other
    modules (review, profile, puzzles) call this and can rely on getting a finished analysis or a
    row whose status is 'failed' with an error."""
    async with session_factory()() as session:
        row = await get_analysis_row(session, game_id)
        if row is not None and row.status == "done":
            return to_game_analysis(row)
    key = job_key(game_id)
    job = runner.get(key)
    if job is not None and job.status in ("pending", "running"):
        await runner.wait(key, timeout=600.0)
        async with session_factory()() as session:
            row = await get_analysis_row(session, game_id)
            if row is not None and row.status == "done":
                return to_game_analysis(row)
    return await run_analysis(game_id, depth, multipv)
