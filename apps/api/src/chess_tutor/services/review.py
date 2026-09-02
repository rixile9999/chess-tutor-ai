"""Review orchestration: layers 2-4 assembled for one move of one game.

`build_move_review` takes a finished `GameAnalysis` and produces a `MoveReviewOut`: the
refutation of a bad move (punishing line, branches, motifs, the "why not X" note), the engine
alternatives with a reason each, the divergence comparison, the human view, the strategy view,
board arrows and the verified explanation. Every number and square comes from the engine lines
or from python-chess; the prose is produced by `services.verbalize` and gated by the verifier.

Payloads are cached in `models.MoveReview` per (game, ply) and reused while the rating and the
analysis depth they were built for stay the same.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable

import chess
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models, schemas
from chess_tutor.motifs import Motif, detect
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import games as games_svc
from chess_tutor.services import maia as maia_svc
from chess_tutor.services import reasoning, verbalize
from chess_tutor.services.verbalize import ReviewFacts, fmt_score, josa, move_label
from chess_tutor.values import PIECE_VALUE
from chess_tutor.verify import Claim

log = logging.getLogger(__name__)

PUNISHABLE: tuple[schemas.Classification, ...] = ("inaccuracy", "mistake", "blunder")
BRANCHES = 3
"""Opponent replies to the punishing move that get a branch."""
BRANCH_PLIES = 3
"""Plies shown per branch: the reply, the follow-up, the next reply."""
SETTLE_PLIES = 8
"""Material for a branch label is read after at most this many plies, once exchanges settle."""
MATE_WON = "메이트 승리"
"""Branch label when the line ends in mate delivered by the side the label is written for."""
MATE_LOST = "메이트"
"""Branch label when that side is the one being mated."""
LINE_PLIES = 6
NOTE_GAP = 1.0
"""The 'why not X' note needs the second-best punishment to be this much worse (pawns)."""
STRATEGY_DEPTH = 10
"""Cap for the engine depth used by the strategy counterfactual; reviews must stay quick."""
RATING_KEY = "_rating"
DEPTH_KEY = "_depth"

Analyse = Callable[[str, int, int], list[schemas.EngineLine]]


# ---------- small helpers ----------


def _color(color: schemas.Color) -> chess.Color:
    return chess.WHITE if color == "white" else chess.BLACK


def _pawns(score: schemas.Score, pov: chess.Color) -> float:
    value = score.as_pawns()
    return value if pov == chess.WHITE else -value


def _moves(board: chess.Board, line: schemas.EngineLine) -> list[chess.Move]:
    """Moves of an engine line that are legal from `board`, parsed from pv_uci or pv."""
    out: list[chess.Move] = []
    probe = board.copy()
    tokens = line.pv_uci or line.pv
    for token in tokens:
        try:
            move = chess.Move.from_uci(token) if line.pv_uci else probe.parse_san(token)
        except ValueError:
            break
        if not probe.is_legal(move):
            break
        out.append(move)
        probe.push(move)
    return out


def _motif_out(motif: Motif) -> schemas.MotifOut:
    return schemas.MotifOut.model_validate(motif.as_dict())


def _material(board: chess.Board, color: chess.Color) -> int:
    return reasoning.material(board, color)


async def _lines(fen: str, depth: int, multipv: int) -> list[schemas.EngineLine]:
    return await analysis_svc.get_lines(fen, depth=depth, multipv=multipv)


def _analyse_with(depth: int) -> Analyse:
    """Sync engine access for the strategy counterfactual; failures become 'no lines'."""

    def analyse(fen: str, requested: int, multipv: int) -> list[schemas.EngineLine]:
        try:
            return analysis_svc.analyse_position(fen, min(requested, depth), multipv)
        except (RuntimeError, ValueError) as exc:
            log.warning("engine unavailable for counterfactual: %s", exc)
            return []

    return analyse


# ---------- refutation ----------


def settle(
    board: chess.Board, moves: list[chess.Move], min_plies: int = BRANCH_PLIES
) -> tuple[chess.Board, int]:
    """Play a line for at least `min_plies`, then keep going only through recaptures on the
    same square (up to SETTLE_PLIES). Returns the end position and the value of the most
    valuable piece of the side to move in `board` that was captured on the way."""
    probe = board.copy()
    mover = board.turn
    biggest = 0
    last_capture: chess.Square | None = None
    for i, move in enumerate(moves[:SETTLE_PLIES]):
        if not probe.is_legal(move):
            break
        is_capture = probe.is_capture(move)
        if i >= min_plies and not (is_capture and move.to_square == last_capture):
            break
        if is_capture:
            victim = probe.piece_at(move.to_square)
            if victim is not None and victim.color == mover:
                biggest = max(biggest, PIECE_VALUE[victim.piece_type])
        last_capture = move.to_square if is_capture else None
        probe.push(move)
    return probe, biggest


def outcome_label(
    board: chess.Board, moves: list[chess.Move], score: schemas.Score, mover: chess.Color
) -> str:
    """Korean label for how a line ends for `mover` (the side to move in `board`): mate either
    way, the material lost once exchanges settle, else the evaluation from the mover's side.

    Material is only meaningful when the line does not end in mate delivered by `mover`: a
    queen sacrifice that mates costs nothing, whatever the material count says."""
    end, biggest = settle(board, moves)
    if end.is_checkmate():
        return MATE_LOST if end.turn == mover else MATE_WON
    if score.mate is not None:
        return MATE_WON if (score.mate > 0) == (mover == chess.WHITE) else MATE_LOST
    lost = _material(board, mover) - _material(end, mover)
    if biggest >= PIECE_VALUE[chess.QUEEN] and lost >= 3:
        return "퀸 상실"
    if lost >= 3:
        return "기물 손실"
    if lost >= 1:
        return "폰 손실"
    pawns = _pawns(score, mover)
    if pawns <= -3.0:
        return "결정적 열세"
    if pawns <= -1.0:
        return "열세"
    return "큰 손실 없음"


def branches_from(
    board: chess.Board, lines: list[schemas.EngineLine], mover: chess.Color
) -> list[schemas.Branch]:
    """Branches after the punishing move: `board` has the punished side (`mover`) to move.
    The first branch is labelled by its own outcome; a later branch with the same outcome
    reads '같은 결과'."""
    out: list[schemas.Branch] = []
    first: str | None = None
    for line in lines[:BRANCHES]:
        moves = _moves(board, line)
        if not moves:
            continue
        label = outcome_label(board, moves, line.score, mover)
        result = "같은 결과" if first is not None and label == first else label
        if first is None:
            first = label
        out.append(schemas.Branch(moves=line.pv[:BRANCH_PLIES], result=result, eval=line.score))
    return out


def note_from(
    board_after: chess.Board, lines_after: list[schemas.EngineLine], punisher: chess.Color
) -> tuple[str | None, list[str]]:
    """'왜 X가 아니라 Y인가: X에는 Z가 있습니다' from the second line after the move, when that
    line is clearly worse for the punisher. Returns the note and [X, Z] for the verifier."""
    if len(lines_after) < 2:
        return None, []
    main, second = lines_after[0], lines_after[1]
    if not main.pv or len(second.pv) < 2 or second.pv[0] == main.pv[0]:
        return None, []
    gap = _pawns(main.score, punisher) - _pawns(second.score, punisher)
    if gap < NOTE_GAP:
        return None, []
    if len(_moves(board_after, second)) < 2:
        return None, []
    x, y, z = second.pv[0], main.pv[0], second.pv[1]
    text = (
        f"왜 {josa(x, '이')} 아니라 {y}인가: {x}에는 {josa(z, '이')} 있습니다. "
        f"{x} 뒤 평가 {fmt_score(second.score)}, {y} 뒤 {fmt_score(main.score)}."
    )
    return text, [x, z]


async def refutation_of(
    move: schemas.MoveAnalysis, lines_after: list[schemas.EngineLine], depth: int
) -> tuple[schemas.Refutation | None, str | None, list[str]]:
    """Refutation of a punishable move plus the FEN after the punishing move and the note
    line. None when the position after the move has no continuation."""
    if not lines_after or not lines_after[0].pv:
        return None, None, []
    board_after = chess.Board(move.fen_after)
    main = lines_after[0]
    punishing = _moves(board_after, main)
    if not punishing:
        return None, None, []
    punished = board_after.copy()
    punished.push(punishing[0])
    motifs = [_motif_out(m) for m in detect(board_after, punishing[0])]
    branches: list[schemas.Branch] = []
    if not punished.is_game_over():
        replies = await _lines(punished.fen(), depth, BRANCHES)
        branches = branches_from(punished, replies, _color(move.color))
    note, note_line = note_from(board_after, lines_after, board_after.turn)
    refutation = schemas.Refutation(
        main_line=main.pv[:LINE_PLIES], branches=branches, motifs=motifs, note=note
    )
    return refutation, punished.fen(), note_line


# ---------- alternatives and comparison ----------


def alternatives_of(
    board: chess.Board,
    lines_before: list[schemas.EngineLine],
    color: schemas.Color,
    played_uci: str | None = None,
) -> list[schemas.Alternative]:
    """Top two engine moves other than the played one, the best of them flagged, each with a
    why. The panel says '대신 두었어야 할 수', so the move the user played never belongs here."""
    out: list[schemas.Alternative] = []
    if not lines_before or not lines_before[0].pv:
        return out
    best_eval = lines_before[0].score
    candidates = [
        line
        for line in lines_before
        if line.pv and not (played_uci and line.pv_uci and line.pv_uci[0] == played_uci)
    ]
    for index, line in enumerate(candidates[:2]):
        moves = _moves(board, line)
        try:
            why, claims = reasoning.explain_alternative(
                board, line.pv[0], moves, line.score, best_eval, color
            )
        except ValueError:
            why, claims = "", []
        out.append(
            schemas.Alternative(
                san=line.pv[0],
                eval=line.score,
                line=line.pv[1 : LINE_PLIES + 1],
                is_best=index == 0 and line is lines_before[0],
                why=why,
                claims=claims,
            )
        )
    return out


def comparison_of(
    board: chess.Board,
    move: schemas.MoveAnalysis,
    lines_before: list[schemas.EngineLine],
    lines_after: list[schemas.EngineLine],
) -> schemas.Comparison | None:
    """Best versus second-best when the played move is one of them, else best versus played
    (the played move's line is the position after it)."""
    if not lines_before or not lines_before[0].pv:
        return None
    best = lines_before[0]
    top2 = [ln.pv_uci[0] for ln in lines_before[:2] if ln.pv_uci]
    if move.uci in top2:
        if len(lines_before) < 2 or not lines_before[1].pv:
            return None
        rival = lines_before[1]
        b_san, pv_b, eval_b = rival.pv[0], _moves(board, rival), rival.score
    else:
        played = chess.Move.from_uci(move.uci)
        after = board.copy()
        after.push(played)
        continuation = _moves(after, lines_after[0]) if lines_after else []
        b_san, pv_b, eval_b = move.san, [played, *continuation], move.eval_after
    try:
        return reasoning.compare_moves(
            board, best.pv[0], b_san, _moves(board, best), pv_b, move.color, best.score, eval_b
        )
    except ValueError as exc:
        log.warning("comparison failed for %s: %s", move.fen_before, exc)
        return None


# ---------- human and strategy views ----------


def human_of(
    move: schemas.MoveAnalysis, rating: int, last_san: str | None
) -> schemas.HumanView | None:
    try:
        return maia_svc.human_view(
            move.fen_before, move.san, move.best_move_san, rating, last_san=last_san
        )
    except Exception as exc:  # noqa: BLE001 - the human view is optional
        log.warning("human view unavailable for %s: %s", move.fen_before, exc)
        return None


def structure_of(board: chess.Board) -> schemas.StructureInfo | None:
    try:
        from chess_tutor import structure
    except ImportError:
        return None
    try:
        return structure.classify(board)
    except Exception as exc:  # noqa: BLE001 - layer 2 must never break the review
        log.warning("structure classification failed: %s", exc)
        return None


async def record_of(
    session: AsyncSession, game: models.Game, structure_key: str | None
) -> dict[str, float | int | None]:
    """Personal record in this structure from the profile workstream, when it exposes
    `structure_record(session, user_id, key)`; empty otherwise."""
    if structure_key is None or game.user_id is None:
        return {}
    try:
        from chess_tutor.services import profile as profile_svc
    except ImportError:
        return {}
    fn = getattr(profile_svc, "structure_record", None)
    if fn is None:
        return {}
    try:
        result = fn(session, game.user_id, structure_key)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 - optional data
        log.warning("structure record unavailable: %s", exc)
        return {}
    return dict(result) if isinstance(result, dict) else {}


def strategy_of(
    boards: list[chess.Board],
    ply: int,
    move: schemas.MoveAnalysis,
    lines_before: list[schemas.EngineLine],
    depth: int,
    structure: schemas.StructureInfo | None,
    record: dict[str, float | int | None],
) -> schemas.StrategyView | None:
    try:
        return reasoning.strategy_view(
            boards,
            ply - 1,
            lines_before,
            move.san,
            move.classification,
            move.color,
            analyse=_analyse_with(depth),
            record=record,
            structure=structure,
            depth=min(depth, STRATEGY_DEPTH),
        )
    except Exception as exc:  # noqa: BLE001 - the strategy tab is optional
        log.warning("strategy view unavailable for ply %s: %s", ply, exc)
        return None


# ---------- arrows ----------


def arrows_of(
    move: schemas.MoveAnalysis,
    refutation: schemas.Refutation | None,
    lines_before: list[schemas.EngineLine],
) -> list[schemas.Arrow]:
    """Played move (ink), punishing move (bad), discovered-attack line (ink, dashed), best
    alternative (good)."""
    played = chess.Move.from_uci(move.uci)
    arrows = [
        schemas.Arrow(
            orig=chess.square_name(played.from_square),
            dest=chess.square_name(played.to_square),
            color="ink",
        )
    ]
    if refutation is not None and refutation.main_line:
        board = chess.Board(move.fen_after)
        try:
            punishing = board.parse_san(refutation.main_line[0])
        except ValueError:
            punishing = None
        if punishing is not None:
            arrows.append(
                schemas.Arrow(
                    orig=chess.square_name(punishing.from_square),
                    dest=chess.square_name(punishing.to_square),
                    color="bad",
                )
            )
        for motif in refutation.motifs:
            if motif.kind == "discovered_attack" and motif.targets:
                arrows.append(
                    schemas.Arrow(
                        orig=motif.attacker, dest=motif.targets[0], color="ink", dashed=True
                    )
                )
    if lines_before and lines_before[0].pv_uci:
        best = chess.Move.from_uci(lines_before[0].pv_uci[0])
        # The good arrow is a move from the position *before* the played move, but the panel
        # draws it on fen_after. Skip it when its origin no longer holds a piece of the mover,
        # which is exactly when the arrow would start on an empty or foreign square.
        after = chess.Board(move.fen_after)
        origin = after.piece_at(best.from_square)
        drawable = origin is not None and origin.color == _color(move.color)
        if best != played and drawable:
            arrows.append(
                schemas.Arrow(
                    orig=chess.square_name(best.from_square),
                    dest=chess.square_name(best.to_square),
                    color="good",
                )
            )
    return arrows


# ---------- facts ----------


def build_facts(
    game_id: int,
    move: schemas.MoveAnalysis,
    *,
    refutation: schemas.Refutation | None,
    fen_punished: str | None,
    note_line: list[str],
    alternatives: list[schemas.Alternative],
    comparison: schemas.Comparison | None,
    human: schemas.HumanView | None,
    strategy: schemas.StrategyView | None,
    played_motifs: list[schemas.MotifOut],
    natural: tuple[str, list[Claim]] | None,
) -> ReviewFacts:
    positions = {"before": move.fen_before, "after": move.fen_after}
    punish_label: str | None = None
    if fen_punished is not None and refutation is not None and refutation.main_line:
        positions["punished"] = fen_punished
        punish_label = move_label(move.fen_after, refutation.main_line[0])
    strategy_note: str | None = None
    structure_name: str | None = None
    if strategy is not None and strategy.structure is not None:
        if strategy.structure.key != "unclassified":
            structure_name = strategy.structure.name
            if strategy.your_move is not None:
                strategy_note = strategy.your_move.note
    reason, claims = natural if natural is not None else (None, [])
    return ReviewFacts(
        game_id=game_id,
        ply=move.ply,
        san=move.san,
        uci=move.uci,
        color=move.color,
        move_label=move_label(move.fen_before, move.san),
        fen_before=move.fen_before,
        fen_after=move.fen_after,
        classification=move.classification,
        eval_before=move.eval_before,
        eval_after=move.eval_after,
        best_san=move.best_move_san,
        natural_reason=reason,
        natural_claims=claims,
        played_motifs=played_motifs,
        refutation=refutation,
        punish_label=punish_label,
        fen_punished=fen_punished,
        note_line=note_line,
        alternatives=alternatives,
        comparison=comparison,
        human=human,
        strategy_note=strategy_note,
        structure_name=structure_name,
        positions=positions,
    )


def natural_of(move: schemas.MoveAnalysis, last_san: str | None) -> tuple[str, list[Claim]]:
    board = chess.Board(move.fen_before)
    return maia_svc.natural_reason(board, chess.Move.from_uci(move.uci), last_san=last_san)


# ---------- assembly ----------


async def _lines_after(
    analysis: schemas.GameAnalysis, ply: int, depth: int
) -> list[schemas.EngineLine]:
    """Lines for the position after ply: stored with the next move, else computed."""
    move = analysis.moves[ply - 1]
    if ply < len(analysis.moves) and analysis.moves[ply].lines:
        return analysis.moves[ply].lines
    try:
        return await _lines(move.fen_after, depth, BRANCHES)
    except (RuntimeError, ValueError) as exc:
        log.warning("no lines after ply %s: %s", ply, exc)
        return []


async def compute_move_review(
    session: AsyncSession,
    game: models.Game,
    analysis: schemas.GameAnalysis,
    ply: int,
    rating: int,
) -> schemas.MoveReviewOut:
    """Build the review without touching the cache."""
    move = analysis.moves[ply - 1]
    depth = analysis.depth or 8
    boards = games_svc.boards_of(game)
    if len(boards) <= ply or boards[ply - 1].fen() != move.fen_before:
        boards = [chess.Board(move.fen_before)]
        board = boards[0]
        ply_index = 1
    else:
        board = boards[ply - 1]
        ply_index = ply
    played = chess.Move.from_uci(move.uci)
    lines_before = move.lines
    last_san = analysis.moves[ply - 2].san if ply >= 2 else None

    refutation: schemas.Refutation | None = None
    fen_punished: str | None = None
    note_line: list[str] = []
    lines_after: list[schemas.EngineLine] = []
    needs_after = move.classification in PUNISHABLE or move.uci not in [
        ln.pv_uci[0] for ln in lines_before[:2] if ln.pv_uci
    ]
    if needs_after:
        lines_after = await _lines_after(analysis, ply, depth)
    if move.classification in PUNISHABLE:
        refutation, fen_punished, note_line = await refutation_of(move, lines_after, depth)

    alternatives = alternatives_of(board, lines_before, move.color, move.uci)
    comparison = comparison_of(board, move, lines_before, lines_after)
    played_motifs = [_motif_out(m) for m in detect(board, played)]
    try:
        natural: tuple[str, list[Claim]] | None = natural_of(move, last_san)
    except ValueError:
        natural = None
    human = await asyncio.to_thread(human_of, move, rating, last_san)
    if human is not None and natural is None and human.natural_reason:
        natural = (human.natural_reason, list(human.claims))

    structure = structure_of(board)
    record = await record_of(session, game, structure.key if structure else None)
    strategy = await asyncio.to_thread(
        strategy_of, boards, ply_index, move, lines_before, depth, structure, record
    )

    facts = build_facts(
        game.id,
        move,
        refutation=refutation,
        fen_punished=fen_punished,
        note_line=note_line,
        alternatives=alternatives,
        comparison=comparison,
        human=human,
        strategy=strategy,
        played_motifs=played_motifs,
        natural=natural,
    )
    explanation = await asyncio.to_thread(verbalize.explain, facts)

    return schemas.MoveReviewOut(
        game_id=game.id,
        ply=ply,
        san=move.san,
        color=move.color,
        fen_before=move.fen_before,
        fen_after=move.fen_after,
        classification=move.classification,
        eval_before=move.eval_before,
        eval_after=move.eval_after,
        refutation=refutation,
        alternatives=alternatives,
        comparison=comparison,
        human=human,
        explanation=explanation,
        strategy=strategy,
        arrows=arrows_of(move, refutation, lines_before),
        highlights=[chess.square_name(played.from_square), chess.square_name(played.to_square)],
        motifs=played_motifs,
    )


# ---------- cache ----------


async def cached_review(
    session: AsyncSession, game_id: int, ply: int, rating: int, depth: int
) -> schemas.MoveReviewOut | None:
    stmt = select(models.MoveReview).where(
        models.MoveReview.game_id == game_id, models.MoveReview.ply == ply
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None or not row.payload:
        return None
    payload = row.payload
    if payload.get(RATING_KEY) != rating or payload.get(DEPTH_KEY) != depth:
        return None
    try:
        return schemas.MoveReviewOut.model_validate(payload)
    except ValueError:
        return None


async def store_review(
    session: AsyncSession, out: schemas.MoveReviewOut, rating: int, depth: int
) -> None:
    payload = out.model_dump(mode="json")
    payload[RATING_KEY] = rating
    payload[DEPTH_KEY] = depth
    stmt = select(models.MoveReview).where(
        models.MoveReview.game_id == out.game_id, models.MoveReview.ply == out.ply
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = models.MoveReview(game_id=out.game_id, ply=out.ply)
        session.add(row)
    row.payload = payload
    row.verified = out.explanation.verified
    try:
        await session.commit()
    except IntegrityError:  # a concurrent request stored the same review first
        await session.rollback()


async def build_move_review(
    session: AsyncSession,
    game: models.Game,
    analysis: schemas.GameAnalysis,
    ply: int,
    rating: int,
) -> schemas.MoveReviewOut:
    """Review of the move at `ply` (1-based), from the cache when one exists for this rating
    and depth. Raises ValueError when the analysis is not done and IndexError for a ply the
    game does not have."""
    if analysis.status != "done":
        raise ValueError(analysis.error or "분석이 끝나지 않았습니다")
    if not 1 <= ply <= len(analysis.moves):
        raise IndexError(f"ply {ply} outside 1..{len(analysis.moves)}")
    depth = analysis.depth
    cached = await cached_review(session, game.id, ply, rating, depth)
    if cached is not None:
        return cached
    out = await compute_move_review(session, game, analysis, ply, rating)
    await store_review(session, out, rating, depth)
    return out


def move_list(analysis: schemas.GameAnalysis) -> list[dict[str, object]]:
    """{ply, san, color, classification} per move for the move list."""
    return [
        {"ply": m.ply, "san": m.san, "color": m.color, "classification": m.classification}
        for m in analysis.moves
    ]
