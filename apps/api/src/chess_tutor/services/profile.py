"""Weakness report over a user's recent games (layer 5: personalization).

Everything here is arithmetic over stored analyses (schemas.MoveAnalysis) plus the deterministic
layer-2 detectors (structure.classify, motifs.detect, openings.classify_game). No engine call,
no LLM. Every number in the report can be traced back to the analysis rows it was summed
from, and the Korean summary is assembled only from those numbers.

Conventions:

* the window is ``[now - days, now]`` over ``played_at``, falling back to ``created_at``;
* accuracy, structure, motif and time statistics use only games whose analysis is ``done``;
* repertoire groups use every game in the window (deviation and result need no analysis) and
  take the loss figure from the analysed games of the group;
* rates are fractions in ``0..1``; centipawn losses are per move, capped at ``LOSS_CAP_CP``.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import chess
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chess_tutor import openings, structure
from chess_tutor.config import get_settings
from chess_tutor.models import Game, Puzzle, User
from chess_tutor.motifs import KOREAN_NAMES, detect
from chess_tutor.schemas import (
    Color,
    MotifMiss,
    MoveAnalysis,
    PhaseAccuracy,
    ProfileReport,
    RepertoireHole,
    Score,
    StructureStat,
    TimeStats,
    TrainingSummary,
)
from chess_tutor.services import games as games_svc
from chess_tutor.services import users

try:  # pawn-break catalogue of the openings workstream; the report degrades without it
    from chess_tutor.services.openings_map import BREAKS, first_breaks
except ImportError:  # pragma: no cover - only when that module is missing
    BREAKS = ()
    first_breaks = None  # type: ignore[assignment]

try:
    from chess_tutor.services.analysis import move_accuracy
except ImportError:  # pragma: no cover - keep the report alive without the engine module
    import math

    def move_accuracy(loss: float) -> float:
        raw = 103.1668 * math.exp(-0.04354 * (loss * 100.0)) - 3.1669
        return max(0.0, min(100.0, raw))


log = logging.getLogger(__name__)

Phase = Literal["opening", "middlegame", "endgame"]
Outcome = Literal["win", "draw", "loss"]

ERROR_CLASSIFICATIONS = frozenset({"mistake", "blunder"})
LOSS_CAP_CP = 500
MATE_CP = 10_000
"""Centipawn stand-in for a mate score, the same as services.puzzles."""

OPENING_TAIL_PLIES = 6
"""Plies after the last book move that still count as the opening."""
OPENING_PLIES_WITHOUT_BOOK = 20
ENDGAME_MATERIAL = 13
"""Total non-pawn material (both sides, Q=9 R=5 B/N=3) at or below which a position is an
endgame."""
NON_PAWN_VALUE = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3}

CLOCK_PRESSURE_SECONDS = 30.0
BLUNDER_RATE_BASELINE = 0.09

STRUCTURE_PLY = 20
"""The structure of a game is the one on the board after this ply (or the last ply)."""

DEVIATION_BEFORE_MOVE = 12
REPERTOIRE_MIN_GAMES = 3
REPERTOIRE_LOSS_PLIES = 30
"""avg_loss_cp of a repertoire group covers the first 15 moves (30 plies)."""

BREAK_FROM_MOVE = 10
BREAK_TO_MOVE = 30
"""Only breaks in this move window count, as in openings_map.break_timing."""

TOP_MOTIFS = 5
TRAINING_MOTIF_SETS = 3
STUDY_STRUCTURES = 2

PHASE_NAMES_KO: dict[Phase, str] = {
    "opening": "오프닝",
    "middlegame": "미들게임",
    "endgame": "엔드게임",
}

BASELINES: tuple[tuple[int, int, tuple[float, float, float]], ...] = (
    (0, 1200, (74.0, 66.0, 56.0)),
    (1200, 1400, (78.0, 72.0, 61.0)),
    (1400, 1600, (82.0, 77.0, 66.0)),
    (1600, 1800, (85.0, 80.0, 71.0)),
    (1800, 2000, (88.0, 83.0, 75.0)),
    (2000, 10_000, (90.0, 86.0, 79.0)),
)
"""Typical (opening, middlegame, endgame) accuracy per rating band. Plausible placeholders
until they are measured on the Lichess database; the band label goes out with the report."""


UserNotFound = users.UserNotFound
"""Re-exported so /profile keeps its import; the resolver lives in services.users."""


def now_utc() -> datetime:
    """Naive UTC, the convention of models.utcnow and services.puzzles."""
    return datetime.now(UTC).replace(tzinfo=None)


# ---------- per-game data ----------


@dataclass
class GameData:
    """One game of the window, with everything the aggregations read."""

    game: Game
    color: Color | None
    """The user's colour, from Game.user_color or the player names."""
    outcome: Outcome | None
    analysed: list[MoveAnalysis] | None
    """Moves of a completed analysis; None when the game was not analysed (or unreadable)."""
    moves: list[chess.Move] = field(default_factory=list)
    """Mainline from the PGN, or replayed from the analysis when the PGN is not readable."""
    standard_start: bool = True

    @property
    def user_moves(self) -> list[MoveAnalysis]:
        if self.analysed is None or self.color is None:
            return []
        return [m for m in self.analysed if m.color == self.color]


def user_color_of(game: Game, username: str) -> Color | None:
    if game.user_color in ("white", "black"):
        return game.user_color  # type: ignore[return-value]
    name = username.strip().lower()
    if game.white.strip().lower() == name:
        return "white"
    if game.black.strip().lower() == name:
        return "black"
    return None


def outcome_of(result: str | None, color: Color | None) -> Outcome | None:
    if color is None or result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if result == "1/2-1/2":
        return "draw"
    return "win" if (result == "1-0") == (color == "white") else "loss"


def analysed_moves(game: Game) -> list[MoveAnalysis] | None:
    row = game.analysis
    if row is None or row.status != "done" or not row.moves:
        return None
    try:
        return [MoveAnalysis.model_validate(m) for m in row.moves]
    except ValidationError:
        log.warning("analysis of game %s is unreadable; skipped in the profile", game.id)
        return None


def _replay_analysis(moves: list[MoveAnalysis]) -> list[chess.Move]:
    """The longest legal prefix of an analysis replayed from the standard start."""
    board = chess.Board()
    out: list[chess.Move] = []
    for m in sorted(moves, key=lambda x: x.ply):
        try:
            move = chess.Move.from_uci(m.uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        out.append(move)
        board.push(move)
    return out


def load_game(game: Game, username: str) -> GameData:
    color = user_color_of(game, username)
    analysed = analysed_moves(game)
    infos = games_svc.game_moves(game)
    if infos:
        moves = [chess.Move.from_uci(i.uci) for i in infos]
        standard = games_svc.initial_fen_of(game) == chess.STARTING_FEN
    elif analysed is not None:
        moves = _replay_analysis(analysed)
        standard = bool(moves)
    else:
        moves, standard = [], False
    return GameData(
        game=game,
        color=color,
        outcome=outcome_of(game.result, color),
        analysed=analysed,
        moves=moves,
        standard_start=standard,
    )


# ---------- move arithmetic ----------


def cp_for(score: Score, color: Color) -> int:
    """Centipawns from ``color``'s point of view; a mate counts as +/-MATE_CP."""
    if score.mate:
        cp = MATE_CP if score.mate > 0 else -MATE_CP
    else:
        cp = score.cp or 0
    return cp if color == "white" else -cp


def cp_loss(move: MoveAnalysis) -> int:
    """Evaluation lost by the mover, in centipawns, never negative and capped at LOSS_CAP_CP.
    The engine's own best move loses nothing, as in services.analysis."""
    if move.best_move_uci is not None and move.uci == move.best_move_uci:
        return 0
    drop = cp_for(move.eval_before, move.color) - cp_for(move.eval_after, move.color)
    return max(0, min(LOSS_CAP_CP, drop))


def is_error(move: MoveAnalysis) -> bool:
    return move.classification in ERROR_CLASSIFICATIONS


def non_pawn_material(board: chess.Board) -> int:
    return sum(
        value * len(board.pieces(piece, color))
        for piece, value in NON_PAWN_VALUE.items()
        for color in (chess.WHITE, chess.BLACK)
    )


def opening_until(moves: list[MoveAnalysis]) -> int:
    """Last ply that belongs to the opening: the last book ply plus OPENING_TAIL_PLIES, or
    OPENING_PLIES_WITHOUT_BOOK when the game never followed a named opening."""
    last_book = max((m.ply for m in moves if m.classification == "book"), default=0)
    return last_book + OPENING_TAIL_PLIES if last_book else OPENING_PLIES_WITHOUT_BOOK


def phase_of(move: MoveAnalysis, until: int) -> Phase:
    """Endgame by material first (an endgame never turns back into an opening), then the
    opening window, then middlegame."""
    try:
        board = chess.Board(move.fen_before)
    except ValueError:
        return "middlegame"
    if non_pawn_material(board) <= ENDGAME_MATERIAL:
        return "endgame"
    if move.ply <= until:
        return "opening"
    return "middlegame"


def _mean(values: list[float], digits: int = 1) -> float:
    return round(sum(values) / len(values), digits) if values else 0.0


# ---------- phases ----------


def rating_band(rating: int) -> tuple[str, tuple[float, float, float]]:
    for low, high, baseline in BASELINES:
        if low <= rating < high:
            return f"{low}-{high}" if high < 10_000 else f"{low}+", baseline
    low, high, baseline = BASELINES[-1]
    return f"{low}+", baseline


def phase_accuracy(games: list[GameData], rating: int) -> PhaseAccuracy | None:
    """Mean per-move accuracy of the user's moves in each phase, and the gap to the rating
    band's baseline. None when no analysed move of the user exists."""
    buckets: dict[Phase, list[float]] = {"opening": [], "middlegame": [], "endgame": []}
    for data in games:
        if data.analysed is None:
            continue
        until = opening_until(data.analysed)
        for move in data.user_moves:
            buckets[phase_of(move, until)].append(move_accuracy(move.win_prob_loss))
    if not any(buckets.values()):
        return None
    band, baseline = rating_band(rating)
    means = {phase: _mean(values) for phase, values in buckets.items()}

    def delta(phase: Phase, base: float) -> float | None:
        return round(means[phase] - base, 1) if buckets[phase] else None

    return PhaseAccuracy(
        opening=means["opening"],
        middlegame=means["middlegame"],
        endgame=means["endgame"],
        delta_opening=delta("opening", baseline[0]),
        delta_middlegame=delta("middlegame", baseline[1]),
        delta_endgame=delta("endgame", baseline[2]),
        opening_moves=len(buckets["opening"]),
        middlegame_moves=len(buckets["middlegame"]),
        endgame_moves=len(buckets["endgame"]),
        baseline_band=band,
    )


# ---------- structures ----------


def structure_key_of(moves: list[MoveAnalysis]) -> str | None:
    """Structure on the board after STRUCTURE_PLY (or the last ply of a shorter game)."""
    ordered = sorted(moves, key=lambda m: m.ply)
    if not ordered:
        return None
    target = ordered[min(STRUCTURE_PLY, len(ordered)) - 1]
    try:
        return structure.classify(chess.Board(target.fen_after)).key
    except ValueError:
        return None


_BREAK_SIDE: dict[str, str] = {b.label: b.side for b in BREAKS}


def user_breaks(data: GameData) -> dict[str, int]:
    """Break label -> move number of the user's own first break inside the counting window."""
    if first_breaks is None or data.color is None or not data.standard_start or not data.moves:
        return {}
    found: dict[str, int] = {}
    for label, move_no in first_breaks(data.moves).items():
        if _BREAK_SIDE.get(label) == data.color and BREAK_FROM_MOVE <= move_no <= BREAK_TO_MOVE:
            found[label] = move_no
    return found


def typical_break(games: list[GameData]) -> tuple[str, float] | None:
    """The user's most frequent break over these games and its average move number."""
    timings: dict[str, list[int]] = defaultdict(list)
    for data in games:
        for label, move_no in user_breaks(data).items():
            timings[label].append(move_no)
    if not timings:
        return None
    label = max(timings, key=lambda k: (len(timings[k]), k))
    return label, _mean([float(v) for v in timings[label]])


def structure_stats(games: list[GameData]) -> list[StructureStat]:
    """Per structure (at STRUCTURE_PLY) over analysed games with a known user colour: games,
    win rate over games with a known result, mean cp loss per user move, break timing."""
    groups: dict[str, list[GameData]] = defaultdict(list)
    for data in games:
        if data.analysed is None or data.color is None:
            continue
        key = structure_key_of(data.analysed)
        if key is None or key == "unclassified":
            continue
        groups[key].append(data)

    stats: list[StructureStat] = []
    for key, members in groups.items():
        outcomes = [d.outcome for d in members if d.outcome is not None]
        wins = outcomes.count("win")
        losses = outcomes.count("loss")
        losses_cp = [float(cp_loss(m)) for d in members for m in d.user_moves]
        brk = typical_break(members)
        stats.append(
            StructureStat(
                key=key,
                name=structure.STRUCTURE_NAMES.get(key, key),
                games=len(members),
                win_rate=round(wins / len(outcomes), 3) if outcomes else 0.0,
                avg_loss_cp=_mean(losses_cp),
                wins=wins,
                losses=losses,
                break_label=brk[0] if brk else None,
                avg_break_move=brk[1] if brk else None,
            )
        )
    stats.sort(key=lambda s: (-s.games, s.win_rate, -s.avg_loss_cp, s.key))
    return stats


def weakest_structures(stats: list[StructureStat]) -> list[StructureStat]:
    """Lowest win rate first; a higher loss per move breaks ties."""
    return sorted(stats, key=lambda s: (s.win_rate, -s.avg_loss_cp, -s.games, s.key))


# ---------- motifs ----------


def motif_kinds(fen: str, uci: str | None) -> set[str]:
    """Kinds of the motifs that ``uci`` creates in ``fen``; empty for anything not legal."""
    if not uci:
        return set()
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
    except ValueError:
        return set()
    if move not in board.legal_moves:
        return set()
    return {m.kind for m in detect(board, move)}


def _position_key(fen: str) -> str:
    return " ".join(fen.split(" ")[:4])


def missed_motifs(games: list[GameData]) -> Counter[str]:
    """For every mistake or blunder of the user: the motifs of the engine's best move in that
    position, plus the motifs of the opponent's punishing reply (top line of the next ply).
    Each kind counts once per error."""
    counts: Counter[str] = Counter()
    for data in games:
        if data.analysed is None:
            continue
        by_ply = {m.ply: m for m in data.analysed}
        for move in data.user_moves:
            if not is_error(move):
                continue
            kinds = motif_kinds(move.fen_before, move.best_move_uci)
            reply = by_ply.get(move.ply + 1)
            if (
                reply is not None
                and reply.lines
                and reply.lines[0].pv_uci
                and _position_key(reply.fen_before) == _position_key(move.fen_after)
            ):
                kinds |= motif_kinds(reply.fen_before, reply.lines[0].pv_uci[0])
            counts.update(kinds)
    return counts


def motif_misses(counts: Counter[str], limit: int) -> list[MotifMiss]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [MotifMiss(kind=kind, count=count) for kind, count in ranked[:limit]]


# ---------- time ----------


def time_stats(games: list[GameData]) -> TimeStats | None:
    """Mistake+blunder rate of the user's moves played with under CLOCK_PRESSURE_SECONDS left
    versus the rest. None when no analysed move carries a clock."""
    under: list[bool] = []
    over: list[bool] = []
    for data in games:
        for move in data.user_moves:
            if move.clock is None:
                continue
            bucket = under if move.clock < CLOCK_PRESSURE_SECONDS else over
            bucket.append(is_error(move))
    if not under and not over:
        return None

    def rate(flags: list[bool]) -> float:
        return round(sum(flags) / len(flags), 3) if flags else 0.0

    return TimeStats(
        blunder_rate_under_30s=rate(under),
        blunder_rate_over_30s=rate(over),
        baseline=BLUNDER_RATE_BASELINE,
        moves_under_30s=len(under),
        moves_over_30s=len(over),
    )


# ---------- repertoire ----------


def repertoire_label(color: Color, moves: list[chess.Move]) -> str | None:
    """Group label: the moves up to and including the opponent's first choice. As White that
    is '1.e4 c5' (my first move, their answer); as Black it is '1.d4 상대' (what I faced)."""
    board = chess.Board()
    sans: list[str] = []
    for move in moves[:2]:
        if move not in board.legal_moves:
            return None
        sans.append(board.san(move))
        board.push(move)
    if color == "white":
        return f"1.{sans[0]} {sans[1]}" if len(sans) == 2 else None
    return f"1.{sans[0]} 상대" if sans else None


def left_book_early(moves: list[chess.Move]) -> bool:
    """True when the game left every named opening before move DEVIATION_BEFORE_MOVE."""
    _, left_at = openings.classify_game(moves)
    if left_at >= len(moves):
        return False
    return (left_at + 2) // 2 < DEVIATION_BEFORE_MOVE


def repertoire_holes(games: list[GameData]) -> list[RepertoireHole]:
    groups: dict[tuple[Color, str], list[GameData]] = defaultdict(list)
    for data in games:
        if data.color is None or not data.standard_start or not data.moves:
            continue
        label = repertoire_label(data.color, data.moves)
        if label is not None:
            groups[(data.color, label)].append(data)

    holes: list[RepertoireHole] = []
    for (_, label), members in groups.items():
        if len(members) < REPERTOIRE_MIN_GAMES:
            continue
        outcomes = [d.outcome for d in members if d.outcome is not None]
        deviated = sum(left_book_early(d.moves) for d in members)
        losses = [
            float(cp_loss(m))
            for d in members
            for m in d.user_moves
            if m.ply <= REPERTOIRE_LOSS_PLIES
        ]
        holes.append(
            RepertoireHole(
                label=label,
                games=len(members),
                deviation_rate=round(deviated / len(members), 3),
                avg_loss_cp=_mean(losses),
                win_rate=round(outcomes.count("win") / len(outcomes), 3) if outcomes else 0.0,
            )
        )
    holes.sort(key=lambda h: (h.win_rate, -h.deviation_rate, -h.games, h.label))
    return holes


# ---------- training ----------


def study_title(stat: StructureStat) -> str:
    if stat.break_label is not None:
        return f"{stat.name} {stat.break_label} 타이밍"
    return f"{stat.name} 계획"


def training_summary(
    due: int, motifs: Counter[str], structures: list[StructureStat]
) -> TrainingSummary:
    return TrainingSummary(
        due_puzzles=due,
        motif_sets=motif_misses(motifs, TRAINING_MOTIF_SETS),
        studies=[study_title(s) for s in weakest_structures(structures)[:STUDY_STRUCTURES]],
    )


# ---------- summary text ----------


def _eul(word: str) -> str:
    """Object particle: 을 after a final consonant, 를 otherwise (non-Hangul endings get 를)."""
    if not word:
        return word
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0:
        return word + "을"
    return word + "를"


def _ro(word: str) -> str:
    """Directional particle: 으로 after a final consonant other than ㄹ, 로 otherwise."""
    if not word:
        return word
    code = ord(word[-1])
    if 0xAC00 <= code <= 0xD7A3:
        final = (code - 0xAC00) % 28
        if final not in (0, 8):  # 8 is ㄹ
            return word + "으로"
    return word + "로"


def _pct(rate: float) -> str:
    return f"{round(rate * 100)}%"


def _move_no(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def summary_text(
    days: int,
    games: int,
    analyzed: int,
    phases: PhaseAccuracy | None,
    structures: list[StructureStat],
    motifs: list[MotifMiss],
    time: TimeStats | None,
) -> str:
    """Two or three sentences from the largest findings, each built from report fields."""
    if games == 0:
        return f"최근 {days}일 동안 가져온 게임이 없습니다. 게임을 가져오면 리포트가 채워집니다."
    if analyzed == 0:
        return (
            f"최근 {days}일 동안 {games}판을 가져왔지만 분석이 끝난 게임이 없습니다. "
            "게임을 분석하면 리포트가 채워집니다."
        )

    sentences: list[str] = []
    worst = weakest_structures(structures)[:1]
    if worst:
        s = worst[0]
        text = (
            f"최근 {days}일 동안 가장 성적이 나쁜 구조는 {_ro(s.name)}, "
            f"{s.games}판 중 {s.wins}승(승률 {_pct(s.win_rate)})에 "
            f"수당 평균 {_move_no(s.avg_loss_cp)}센티폰을 잃었"
        )
        if s.break_label is not None and s.avg_break_move is not None:
            text += (
                f"으며 {s.break_label} 브레이크는 평균 {_move_no(s.avg_break_move)}수에 나왔습니다."
            )
        else:
            text += "습니다."
        sentences.append(text)
    if motifs:
        top = motifs[0]
        label = KOREAN_NAMES.get(top.kind, top.kind)
        sentences.append(f"전술에서는 {_eul(label)} {top.count}번 놓쳤습니다.")
    if (
        time is not None
        and time.moves_under_30s > 0
        and time.blunder_rate_under_30s > time.blunder_rate_over_30s
    ):
        sentences.append(
            f"30초 미만에 둔 수 {time.moves_under_30s}개의 블런더율은 "
            f"{_pct(time.blunder_rate_under_30s)}로, 30초 이상일 때의 "
            f"{_pct(time.blunder_rate_over_30s)}보다 높습니다."
        )
    elif phases is not None:
        candidates: list[tuple[float | None, Phase, float]] = [
            (phases.delta_opening, "opening", phases.opening),
            (phases.delta_middlegame, "middlegame", phases.middlegame),
            (phases.delta_endgame, "endgame", phases.endgame),
        ]
        deltas = [(d, phase, acc) for d, phase, acc in candidates if d is not None and d < 0]
        if deltas:
            d, phase, acc = min(deltas)
            sentences.append(
                f"{PHASE_NAMES_KO[phase]} 정확도는 {acc:.0f}점으로 같은 구간 기준보다 "
                f"{abs(d):.0f}점 낮습니다."
            )
    if not sentences:
        sentences.append(
            f"최근 {days}일 동안 분석한 {analyzed}판에서 눈에 띄는 약점이 잡히지 않았습니다."
        )
    return " ".join(sentences[:3])


# ---------- database ----------


async def find_users(session: AsyncSession, username: str) -> list[User]:
    """Every account with this name, platform accounts before local ones.

    The one resolver, shared with training, so a profile and a puzzle deck never disagree
    about which accounts a name covers."""
    return await users.find_users(session, username)


async def games_in_window(
    session: AsyncSession, user_ids: list[int], window_from: datetime
) -> list[Game]:
    stmt = (
        select(Game)
        .options(selectinload(Game.analysis))
        .where(
            Game.user_id.in_(user_ids),
            func.coalesce(Game.played_at, Game.created_at) >= window_from,
        )
        .order_by(Game.played_at.desc().nulls_last(), Game.id.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def due_puzzle_count(session: AsyncSession, user_ids: list[int], now: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(Puzzle)
        .where(Puzzle.user_id.in_(user_ids), Puzzle.due_at <= now)
    )
    return int((await session.execute(stmt)).scalar_one())


def rating_of(users: list[User], games: list[GameData]) -> int:
    """Rapid rating, else blitz, else the mean of the user's Elo in the window's games, else
    the configured default."""
    for attr in ("rating_rapid", "rating_blitz"):
        for user in users:
            value = getattr(user, attr)
            if value:
                return int(value)
    elos = [
        (g.game.white_elo if g.color == "white" else g.game.black_elo)
        for g in games
        if g.color is not None
    ]
    known = [e for e in elos if e]
    if known:
        return round(sum(known) / len(known))
    return get_settings().default_rating


# ---------- entry point ----------


def report_from(
    username: str,
    platform: str,
    games: list[GameData],
    *,
    days: int,
    now: datetime,
    rating: int,
    due_puzzles: int,
    rating_rapid: int | None = None,
    rating_blitz: int | None = None,
) -> ProfileReport:
    """Pure aggregation over loaded games; build_report feeds it from the database."""
    analysed = [g for g in games if g.analysed is not None]
    phases = phase_accuracy(analysed, rating)
    structures = structure_stats(analysed)
    motif_counts = missed_motifs(analysed)
    motifs = motif_misses(motif_counts, TOP_MOTIFS)
    time = time_stats(analysed)
    return ProfileReport(
        username=username,
        platform=platform,
        rating_rapid=rating_rapid,
        rating_blitz=rating_blitz,
        window_from=now - timedelta(days=days),
        window_to=now,
        games=len(games),
        analyzed_games=len(analysed),
        summary_text=summary_text(
            days, len(games), len(analysed), phases, structures, motifs, time
        ),
        phase_accuracy=phases,
        structures=structures,
        motifs_missed=motifs,
        time=time,
        training=training_summary(due_puzzles, motif_counts, structures),
        repertoire_holes=repertoire_holes(games),
    )


async def build_report(session: AsyncSession, username: str, days: int = 60) -> ProfileReport:
    """Weakness report over the user's games of the last ``days`` days. Raises UserNotFound
    when no account carries the name."""
    users = await find_users(session, username)
    if not users:
        raise UserNotFound(username)
    now = now_utc()
    ids = [u.id for u in users]
    rows = await games_in_window(session, ids, now - timedelta(days=days))
    games = [load_game(row, username) for row in rows]
    due = await due_puzzle_count(session, ids, now)
    primary = users[0]
    return report_from(
        primary.username,
        primary.platform,
        games,
        days=days,
        now=now,
        rating=rating_of(users, games),
        due_puzzles=due,
        rating_rapid=next((u.rating_rapid for u in users if u.rating_rapid), None),
        rating_blitz=next((u.rating_blitz for u in users if u.rating_blitz), None),
    )
