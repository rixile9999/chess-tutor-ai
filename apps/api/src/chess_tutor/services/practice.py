"""Practice games on disk: PGN assembly, the `practice` Game row, and the plan report.

save() turns what the board screen played into a PGN, stores it through the ordinary import
path (so a practice game is an ordinary Game row with source "practice") and queues its
analysis. plan_report() answers, after the analysis, which of the structure's typical plans
the user actually carried out.

Two decisions worth naming:

* A manual game (``user_color`` None, both sides played by hand) is stored with the user as
  White and "수동" as Black, and header ``Mode`` "manual". The user has to be one of the two
  names or ``upsert_games`` cannot attach the game to the account, and White is the arbitrary
  half of that choice; the Mode header is what tells the screens the game was not a contest.
* ``games.parse_pgn`` derives ``source_id`` from a hash of the PGN, so two identical games
  would collide and the second would count as "skipped". Every save therefore carries a UTC
  timestamp with seconds and a ``PracticeNonce`` (uuid4) header, which makes each save a new
  row even when the same moves are saved twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import chess
import chess.pgn
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import openings, structure
from chess_tutor.models import Game
from chess_tutor.schemas import (
    AnalysisStatus,
    Color,
    Plan,
    PlanReport,
    PracticeGameIn,
    PracticeGameOut,
    PracticeMode,
    StructureInfo,
)
from chess_tutor.services import analysis, games, users
from chess_tutor.services import openings_catalog as catalog
from chess_tutor.services import plans as kb

EVENT = "chess-tutor practice"
SITE = "chess-tutor"
SOURCE = "practice"
MANUAL_OPPONENT = "수동"
OPPONENT_LABELS = {"maia": "Maia", "stockfish": "Stockfish"}
DEFAULT_TERMINATION = "unfinished"

TERMINATIONS: dict[str, str] = {
    "checkmate": "checkmate",
    "stalemate": "stalemate",
    "insufficient_material": "material",
    "seventyfive_moves": "fifty",
    "fivefold_repetition": "repetition",
    "fifty_moves": "fifty",
    "threefold_repetition": "repetition",
}

SIDE_NAMES_KO: dict[str, str] = {"white": "백", "black": "흑"}
PRACTICE_MODES = ("free", "drill", "tabiya")


# ---------- PGN assembly ----------


def opponent_display(req: PracticeGameIn) -> str:
    """'Maia 1500' / 'Stockfish 1800', or '수동' when nobody played the other side."""
    if req.opponent is None:
        return MANUAL_OPPONENT
    label = OPPONENT_LABELS.get(req.opponent.kind, req.opponent.kind)
    return f"{label} {req.opponent.rating}"


def _mode(req: PracticeGameIn) -> str:
    if req.user_color is None:
        return "manual"
    return "ai-black" if req.user_color == "white" else "ai-white"


def _players(req: PracticeGameIn) -> tuple[str, str]:
    """(White, Black). A manual game puts the user on White; see the module docstring."""
    other = opponent_display(req)
    if req.user_color == "black":
        return other, req.username
    return req.username, other


def _elos(req: PracticeGameIn, user_rating: int | None) -> tuple[int | None, int | None]:
    other = req.opponent.rating if req.opponent is not None else None
    if req.user_color == "black":
        return other, user_rating
    return user_rating, other


def replay(start_fen: str, moves_san: list[str]) -> tuple[chess.Board, list[chess.Move]]:
    """The start position and every move, validated. Raises ValueError naming the ply."""
    try:
        start = chess.Board(start_fen)
    except ValueError as exc:
        raise ValueError(f"시작 국면(FEN)을 읽을 수 없습니다: {exc}") from exc
    if not start.is_valid():
        raise ValueError("시작 국면이 체스 규칙에 맞지 않습니다.")
    board = start.copy()
    moves: list[chess.Move] = []
    for index, san in enumerate(moves_san):
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            raise ValueError(f"{index + 1}번째 수 '{san}'을 둘 수 없습니다: {exc}") from exc
        moves.append(move)
        board.push(move)
    return start, moves


def result_of(board: chess.Board, asked: str) -> tuple[str, str]:
    """(result, termination) — the board wins over the request when the game really ended."""
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return asked, DEFAULT_TERMINATION
    reason = TERMINATIONS.get(outcome.termination.name.lower(), outcome.termination.name.lower())
    return outcome.result(), reason


def build_pgn(req: PracticeGameIn, user_rating: int | None = None) -> str:
    """The PGN of one practice game, headers filled in. Raises ValueError on an illegal move."""
    start, moves = replay(req.start_fen, req.moves_san)
    final = start.copy()
    for move in moves:
        final.push(move)

    game = chess.pgn.Game()
    if start.fen() != games.STANDARD_START:
        game.setup(start)
    white, black = _players(req)
    white_elo, black_elo = _elos(req, user_rating)
    result, termination = result_of(final, req.result)
    now = datetime.now(UTC)

    headers: dict[str, str | None] = {
        "Event": EVENT,
        "Site": SITE,
        "Date": now.strftime("%Y.%m.%d"),
        "Round": "-",
        "White": white,
        "Black": black,
        "Result": result,
        "UTCDate": now.strftime("%Y.%m.%d"),
        "UTCTime": now.strftime("%H:%M:%S"),
        "WhiteElo": str(white_elo) if white_elo else None,
        "BlackElo": str(black_elo) if black_elo else None,
        "TimeControl": req.time_control,
        "Mode": _mode(req),
        "Opponent": (
            f"{req.opponent.kind}:{req.opponent.rating}" if req.opponent is not None else "manual"
        ),
        "CoachPreset": req.coach.preset,
        "Hints": str(req.coach.hints),
        "Takebacks": str(req.coach.takebacks),
        "Alerts": str(req.coach.alerts),
        "PracticeMode": req.practice_mode,
        "OpeningId": req.opening_id,
        "Termination": req.termination or termination,
        "PracticeNonce": uuid.uuid4().hex,
    }
    book, _ = openings.classify_game(moves, start=start)
    if book is not None:
        headers["ECO"] = book.eco
        headers["Opening"] = book.name
    for key, value in headers.items():
        if value is not None:
            game.headers[key] = value

    node: chess.pgn.GameNode = game
    clocks = req.clocks or []
    for index, move in enumerate(moves):
        node = node.add_variation(move)
        if index < len(clocks):
            node.set_clock(clocks[index])
    game.headers["Result"] = result
    return str(game)


# ---------- saving ----------


async def user_rating(session: AsyncSession, username: str) -> int | None:
    """Rapid, else blitz rating of the account with this name. Reading never creates one, so an
    unknown name simply has no Elo in the PGN."""
    for account in await users.find_users(session, username):
        rating = account.rating_rapid or account.rating_blitz
        if rating:
            return int(rating)
    return None


async def save(session: AsyncSession, req: PracticeGameIn) -> PracticeGameOut:
    """Store the game as source "practice" and, when asked, queue its analysis."""
    if not req.moves_san:
        raise ValueError("저장할 수가 없습니다.")
    pgn = build_pgn(req, await user_rating(session, req.username))
    report = games.parse_pgn(pgn)
    if not report.games:
        raise ValueError(report.errors[0] if report.errors else "PGN을 만들지 못했습니다.")
    stored = await games.upsert_games(session, report.games, SOURCE, username=req.username)
    if not stored.game_ids:
        raise ValueError("이미 저장된 게임입니다.")
    game_id = stored.game_ids[0]
    status: AnalysisStatus = "none"
    if req.analyse:
        analysis.submit_analysis(game_id)
        status = "pending"
    return PracticeGameOut(game_id=game_id, analysis_status=status, pgn=pgn)


# ---------- plan report ----------


@dataclass(frozen=True)
class _Played:
    board: chess.Board
    """Position before the move."""
    move: chess.Move
    move_no: int


def _side_of(game: Game) -> Color:
    return "black" if game.user_color == "black" else "white"


def _played_by(start: chess.Board, moves: list[chess.Move], side: Color) -> list[_Played]:
    color = chess.WHITE if side == "white" else chess.BLACK
    board = start.copy()
    out: list[_Played] = []
    for move in moves:
        if board.turn == color:
            out.append(_Played(board.copy(stack=False), move, board.fullmove_number))
        board.push(move)
    return out


def structure_board(
    start: chess.Board, moves: list[chess.Move], opening_id: str | None
) -> chess.Board:
    """The position the structure is read from: the catalogue tabiya when the game names one,
    else the first position of the game the classifier recognises, else the final one."""
    if opening_id:
        tabiya = catalog.tabiya_board(opening_id)
        if tabiya is not None:
            return tabiya
    board = start.copy()
    if structure.key_at(board) != "unclassified":
        return board
    for move in moves:
        board.push(move)
        if structure.key_at(board) != "unclassified":
            return board.copy(stack=False)
    return board


def _pvs(row_lines: list[list[chess.Move]]) -> list[list[chess.Move]]:
    return [pv for pv in row_lines if pv]


def _line_moves(board: chess.Board, ucis: list[str]) -> list[chess.Move]:
    """An engine line as moves, stopping at the first move that does not fit the board."""
    out: list[chess.Move] = []
    b = board.copy()
    for uci in ucis:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if not b.is_legal(move):
            break
        out.append(move)
        b.push(move)
    return out


def played_breaks(plans: list[Plan], played: list[_Played]) -> list[str]:
    """Pawn breaks of these plans that the side actually played, as 'e4 (12수)'."""
    seen: dict[str, str] = {}
    for plan in plans:
        for hint in kb.break_hints(plan):
            for step in played:
                if not kb.hint_matches_move(hint, step.board, step.move):
                    continue
                san = step.board.san(step.move)
                seen.setdefault(san, f"{san} ({step.move_no}수)")
    return list(seen.values())


def summarise(
    info: StructureInfo,
    side: Color,
    executed: list[Plan],
    later: list[Plan],
    unavailable: list[Plan],
    breaks: list[str],
) -> str:
    """Korean summary built only from the report's own fields."""
    side_ko = SIDE_NAMES_KO[side]
    total = len(executed) + len(later) + len(unavailable)
    if total == 0:
        return f"{info.name} 구조라 이 구조의 계획 목록이 없습니다."
    parts = [
        f"{info.name} 구조에서 {side_ko}의 계획 {total}개 중 {len(executed)}개를 실행했습니다."
    ]
    if executed:
        parts.append("실행한 계획: " + ", ".join(p.title for p in executed) + ".")
    if later:
        parts.append("아직 남은 계획: " + ", ".join(p.title for p in later) + ".")
    if unavailable:
        parts.append(f"이제 둘 수 없게 된 계획은 {len(unavailable)}개입니다.")
    if breaks:
        parts.append("폰 브레이크는 " + ", ".join(breaks) + "를 뒀습니다.")
    else:
        parts.append("계획의 폰 브레이크는 두지 않았습니다.")
    return " ".join(parts)


async def plan_report(session: AsyncSession, game_id: int) -> PlanReport | None:
    """Which typical plans of the structure the user executed. None when the game is unknown."""
    game = await games.get_game(session, game_id)
    if game is None:
        return None
    side = _side_of(game)
    headers = game.headers or {}
    opening_id = headers.get("OpeningId")
    opening_id = opening_id if isinstance(opening_id, str) and opening_id else None
    mode = headers.get("PracticeMode")
    practice_mode: PracticeMode | None = mode if mode in PRACTICE_MODES else None
    entry = catalog.find(opening_id) if opening_id else None
    opening_name = entry.entry.name_ko if entry is not None else game.opening_name

    try:
        start, plies = analysis.walk_pgn(game.pgn)
    except ValueError:
        start, plies = chess.Board(), []
    moves = [ply.move for ply in plies]

    report = PlanReport(
        game_id=game_id,
        side=side,
        opening_id=opening_id,
        opening_name=opening_name,
        practice_mode=practice_mode,
    )
    if not moves:
        report.summary = "수가 없어 계획을 판단할 수 없습니다."
        return report

    stored = await analysis.get_or_analyze(game_id, depth=None, multipv=None)
    rows = stored.moves if stored.status == "done" else []

    board = start.copy()
    for move in moves:
        board.push(move)
    pvs: list[list[chess.Move]] = []
    if rows:
        last = rows[-1]
        board = chess.Board(last.fen_before)
        pvs = _pvs([_line_moves(board, line.pv_uci) for line in last.lines])

    # The structure (and with it the orientation of the plans) is read from the tabiya, not
    # from the position the report is written at, so a later exchange cannot flip it.
    reference = structure_board(start, moves, opening_id)
    info = structure.classify(reference)
    report.structure = info
    matched = kb.match_plans(
        info.key,
        side,
        pvs,
        board,
        moves,
        mirror=kb.mirrored(info.key, reference),
        start_board=start,
    )
    report.executed = [p for p in matched if p.status == "executed"]
    report.pv_match = [p for p in matched if p.status == "pv_match"]
    report.later = [p for p in matched if p.status == "later"]
    report.unavailable = [p for p in matched if p.status == "unavailable"]
    report.breaks = played_breaks(
        report.executed + report.pv_match + report.later, _played_by(start, moves, side)
    )
    report.summary = summarise(
        info,
        side,
        report.executed,
        report.pv_match + report.later,
        report.unavailable,
        report.breaks,
    )
    return report
