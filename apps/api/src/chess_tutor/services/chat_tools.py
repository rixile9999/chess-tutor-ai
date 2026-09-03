"""The chess tools the position chat exposes to Claude Code over MCP.

Every tool is a thin wrapper over layers 1-3 (engine cache, motif detectors, structure and
feature code, Maia) plus python-chess, so the model can only state what those compute. The
one tool with a side effect, `show_board`, pushes a board state into the chat session named
by the request's `X-Chat-Session` header; the review page renders it in the answer stream.

The server is mounted into the FastAPI app (api.py) so the tools share the engine pool, the
cache and the database. The `*_impl` functions are the tools without the transport, for
tests and for callers inside the process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import chess
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from chess_tutor import features as features_mod
from chess_tutor import schemas
from chess_tutor.config import get_settings
from chess_tutor.motifs import detect
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import chat as chat_svc
from chess_tutor.services import maia as maia_svc
from chess_tutor.services import reasoning
from chess_tutor.services.chat import SERVER_NAME

log = logging.getLogger(__name__)

SESSION_HEADER = "x-chat-session"
COMPARE_MULTIPV = 1
REPLY_PLIES = 6
MAX_ARROWS = 8

mcp = MCPServer(
    SERVER_NAME,
    instructions=(
        "Chess facts for the tutor: engine lines, move comparison, tactical motifs, human move "
        "probabilities, static features, and a board the student is watching."
    ),
)


# ---------- results ----------


class Line(BaseModel):
    rank: int
    cp: int | None = Field(description="Centipawns from White's point of view.")
    mate: int | None = Field(description="Moves to mate, White's point of view; null if none.")
    win_prob_mover: float = Field(description="Win probability of the side to move, 0..1.")
    pv: list[str] = Field(description="Principal variation in SAN.")


class AnalyseResult(BaseModel):
    fen: str
    side_to_move: str
    depth: int
    best: str | None
    lines: list[Line]


class MoveEval(BaseModel):
    san: str
    fen_after: str
    cp: int | None
    mate: int | None
    win_prob_mover: float
    loss_vs_better: float = Field(
        description="Win-probability loss of the mover against the better move."
    )
    classification: str
    reply_line: list[str] = Field(description="Opponent's best continuation after the move, SAN.")
    why: str = Field(description="One to three Korean sentences on what the move does and costs.")


class CompareResult(BaseModel):
    fen: str
    side_to_move: str
    depth: int
    a: MoveEval
    b: MoveEval
    better: str
    summary: str
    divergence_ply: int | None
    feature_rows: list[schemas.FeatureDiffRow] = Field(
        description="Static differences between the two lines' end positions, mover's view."
    )


class ShowResult(BaseModel):
    shown: bool
    fen: str
    moves: list[str]
    last_move: list[str] | None
    note: str


class MotifsResult(BaseModel):
    fen_after: str
    motifs: list[schemas.MotifOut]


class ProbsResult(BaseModel):
    rating: int
    source: str
    probs: dict[str, float] = Field(description="SAN -> probability, most likely first.")


class FeaturesResult(BaseModel):
    side_to_move: str
    structure: schemas.StructureInfo | None
    rows: list[schemas.FeatureDiffRow] = Field(
        description="Column a is the side to move, b the opponent; delta > 0 favours a."
    )


# ---------- helpers ----------


def _board(fen: str) -> chess.Board:
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"FEN을 읽지 못했습니다: {exc}") from exc
    if not board.is_valid():
        raise ValueError("올바른 국면이 아닙니다 (킹이 없거나 둘 차례가 아닌 쪽이 체크 등).")
    return board


def _parse_move(board: chess.Board, text: str) -> chess.Move:
    text = text.strip()
    try:
        return board.parse_san(text)
    except ValueError:
        pass
    try:
        move = chess.Move.from_uci(text)
    except ValueError as exc:
        raise ValueError(f"'{text}'는 이 국면에서 합법 수가 아닙니다.") from exc
    if move not in board.legal_moves:
        raise ValueError(f"'{text}'는 이 국면에서 합법 수가 아닙니다.")
    return move


def _as_list(value: str | list[str] | None) -> list[str]:
    """Tool arguments that are lists in the schema but sometimes arrive as one string
    ('a1a5, d8a5:good' or 'Qf6 Rxa5'): split on commas and whitespace."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in value.replace(",", " ").split() if part]
    return [str(part) for part in value]


def _depth(requested: int | None) -> int:
    settings = get_settings()
    depth = requested or settings.engine_depth
    return max(4, min(depth, settings.chat_max_depth))


def _color(board: chess.Board) -> schemas.Color:
    return "white" if board.turn else "black"


def _line(board: chess.Board, line: schemas.EngineLine) -> Line:
    return Line(
        rank=line.rank,
        cp=line.score.cp,
        mate=line.score.mate,
        win_prob_mover=round(analysis_svc.win_prob(line.score, _color(board)), 3),
        pv=line.pv,
    )


def _session(ctx: Context | None) -> chat_svc.ChatSession | None:
    headers: Mapping[str, str] | None = None
    if ctx is not None:
        try:
            headers = ctx.headers
        except Exception:  # noqa: BLE001 - stdio or a test context has no request
            headers = None
    if not headers:
        return None
    session_id = headers.get(SESSION_HEADER) or headers.get("X-Chat-Session")
    return chat_svc.get_session(session_id)


# ---------- implementations ----------


async def analyse_impl(
    fen: str, depth: int | None = None, multipv: int | None = None
) -> AnalyseResult:
    board = _board(fen)
    d = _depth(depth)
    lines = await analysis_svc.get_lines(board.fen(), depth=d, multipv=max(1, min(multipv or 3, 5)))
    return AnalyseResult(
        fen=board.fen(),
        side_to_move=_color(board),
        depth=d,
        best=lines[0].pv[0] if lines and lines[0].pv else None,
        lines=[_line(board, ln) for ln in lines],
    )


async def _evaluate(
    board: chess.Board, move: chess.Move, depth: int
) -> tuple[schemas.Score, list[str], list[chess.Move]]:
    after = board.copy()
    after.push(move)
    lines = await analysis_svc.get_lines(after.fen(), depth=depth, multipv=COMPARE_MULTIPV)
    reply = lines[0] if lines else None
    if reply is None:
        raise ValueError("엔진이 줄을 돌려주지 않았습니다.")
    reply_moves: list[chess.Move] = []
    probe = after.copy()
    for uci in reply.pv_uci[:REPLY_PLIES]:
        mv = chess.Move.from_uci(uci)
        if mv not in probe.legal_moves:
            break
        reply_moves.append(mv)
        probe.push(mv)
    return reply.score, reply.pv[: len(reply_moves)], reply_moves


async def compare_impl(fen: str, san_a: str, san_b: str, depth: int | None = None) -> CompareResult:
    board = _board(fen)
    move_a = _parse_move(board, san_a)
    move_b = _parse_move(board, san_b)
    if move_a == move_b:
        raise ValueError("같은 수를 비교할 수 없습니다.")
    d = _depth(depth)
    (eval_a, reply_a, reply_moves_a), (eval_b, reply_b, reply_moves_b) = await asyncio.gather(
        _evaluate(board, move_a, d), _evaluate(board, move_b, d)
    )
    color = _color(board)
    prob_a = analysis_svc.win_prob(eval_a, color)
    prob_b = analysis_svc.win_prob(eval_b, color)
    better_eval = eval_a if prob_a >= prob_b else eval_b
    san_a, san_b = board.san(move_a), board.san(move_b)

    def move_eval(
        san: str,
        move: chess.Move,
        score: schemas.Score,
        reply: list[str],
        reply_moves: list[chess.Move],
    ) -> MoveEval:
        after = board.copy()
        after.push(move)
        loss = analysis_svc.win_prob_loss(better_eval, score, color)
        try:
            why, _ = reasoning.explain_alternative(
                board, san, [move, *reply_moves], score, better_eval, color
            )
        except ValueError:
            why = ""
        return MoveEval(
            san=san,
            fen_after=after.fen(),
            cp=score.cp,
            mate=score.mate,
            win_prob_mover=round(analysis_svc.win_prob(score, color), 3),
            loss_vs_better=round(loss, 3),
            classification=analysis_svc.classify_loss(loss),
            reply_line=reply,
            why=why,
        )

    a = move_eval(san_a, move_a, eval_a, reply_a, reply_moves_a)
    b = move_eval(san_b, move_b, eval_b, reply_b, reply_moves_b)
    try:
        comparison = reasoning.compare_moves(
            board,
            san_a,
            san_b,
            [move_a, *reply_moves_a],
            [move_b, *reply_moves_b],
            color,
            eval_a,
            eval_b,
        )
        rows, summary, div_ply = comparison.rows, comparison.summary, comparison.divergence_ply
    except ValueError as exc:
        log.warning("compare: feature diff failed for %s: %s", fen, exc)
        rows, summary, div_ply = [], "", None
    return CompareResult(
        fen=board.fen(),
        side_to_move=color,
        depth=d,
        a=a,
        b=b,
        better=san_a if prob_a >= prob_b else san_b,
        summary=summary,
        divergence_ply=div_ply,
        feature_rows=rows,
    )


def _parse_arrow(text: str) -> schemas.Arrow:
    body, _, tone = text.strip().partition(":")
    body = body.replace("-", "").replace(" ", "").lower()
    if len(body) != 4 or body[:2] not in chess.SQUARE_NAMES or body[2:] not in chess.SQUARE_NAMES:
        raise ValueError(f"화살표 '{text}'는 'e2e4' 꼴이어야 합니다.")
    color: str = tone.strip().lower() or "ink"
    if color not in ("good", "bad", "ink"):
        color = "ink"
    return schemas.Arrow(orig=body[:2], dest=body[2:], color=color)  # type: ignore[arg-type]


def show_board_impl(
    session: chat_svc.ChatSession | None,
    fen: str,
    moves: str | list[str] | None = None,
    caption: str = "",
    arrows: str | list[str] | None = None,
    highlights: str | list[str] | None = None,
) -> ShowResult:
    board = _board(fen)
    start_fen = board.fen()
    sans: list[str] = []
    last: list[str] | None = None
    for text in _as_list(moves):
        move = _parse_move(board, text)
        sans.append(board.san(move))
        last = [chess.square_name(move.from_square), chess.square_name(move.to_square)]
        board.push(move)
    parsed_arrows = [_parse_arrow(a) for a in _as_list(arrows)[:MAX_ARROWS]]
    squares = [s.strip().lower() for s in _as_list(highlights)]
    bad = [s for s in squares if s not in chess.SQUARE_NAMES]
    if bad:
        raise ValueError(f"칸 이름이 아닙니다: {', '.join(bad)}")
    event: dict[str, Any] = {
        "type": "board",
        "start_fen": start_fen,
        "fen": board.fen(),
        "moves": sans,
        "last_move": last,
        "arrows": [a.model_dump(mode="json") for a in parsed_arrows],
        "highlights": squares,
        "caption": caption.strip(),
    }
    if session is None:
        return ShowResult(
            shown=False,
            fen=board.fen(),
            moves=sans,
            last_move=last,
            note="채팅 세션이 없어 화면에는 표시되지 않았습니다.",
        )
    chat_svc.push_board(session, event)
    return ShowResult(shown=True, fen=board.fen(), moves=sans, last_move=last, note="표시했습니다.")


def motifs_impl(fen: str, san: str) -> MotifsResult:
    board = _board(fen)
    move = _parse_move(board, san)
    found = detect(board, move)
    after = board.copy()
    after.push(move)
    return MotifsResult(
        fen_after=after.fen(),
        motifs=[schemas.MotifOut.model_validate(m.as_dict()) for m in found],
    )


def maia_probs_impl(
    fen: str, rating: int | None = None, include: str | list[str] | None = None
) -> ProbsResult:
    board = _board(fen)
    r = rating or get_settings().default_rating
    sans: list[str] = []
    for text in _as_list(include):
        sans.append(board.san(_parse_move(board, text)))
    probs, source = maia_svc.move_probs(board.fen(), r, sans)
    ordered = dict(sorted(probs.items(), key=lambda kv: -kv[1]))
    return ProbsResult(rating=r, source=source, probs={k: round(v, 3) for k, v in ordered.items()})


def features_impl(fen: str) -> FeaturesResult:
    board = _board(fen)
    color = _color(board)
    structure: schemas.StructureInfo | None
    try:
        from chess_tutor import structure as structure_mod

        structure = structure_mod.classify(board)
    except Exception as exc:  # noqa: BLE001 - optional
        log.warning("features: structure failed: %s", exc)
        structure = None
    rows = features_mod.summarize_features(features_mod.static_features(board), color)
    return FeaturesResult(side_to_move=color, structure=structure, rows=rows)


# ---------- MCP surface ----------


@contextmanager
def _tool_errors() -> Iterator[None]:
    """A ValueError (illegal move, bad FEN, bad square) becomes a tool error whose text the
    model sees, so it can correct itself. Any other exception stays masked by the SDK."""
    try:
        yield
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    description=(
        "Engine analysis of a position (Stockfish, cached). Scores are from White's point "
        "of view; win_prob_mover is for the side to move. Use multipv 3 to see alternatives."
    )
)
async def analyse(fen: str, depth: int | None = None, multipv: int | None = 3) -> AnalyseResult:
    with _tool_errors():
        return await analyse_impl(fen, depth, multipv)


@mcp.tool(
    description=(
        "Show a position on the student's board. Plays `moves` (SAN) from `fen` and displays "
        "the result with the last move highlighted. Call it once per move while narrating a "
        "line; `caption` is one sentence on what the shown move does. `arrows` like "
        "'e2e4' or 'e2e4:bad'; `highlights` are squares."
    )
)
async def show_board(
    fen: str,
    ctx: Context,
    moves: list[str] | str | None = None,
    caption: str = "",
    arrows: list[str] | str | None = None,
    highlights: list[str] | str | None = None,
) -> ShowResult:
    # async so it runs on the event loop: the SDK sends plain functions to a worker thread,
    # and the session queue this pushes into belongs to the loop.
    with _tool_errors():
        return show_board_impl(_session(ctx), fen, moves, caption, arrows, highlights)


@mcp.tool(
    description=(
        "Compare two candidate moves in the same position at the same depth: evaluation, "
        "win-probability loss, classification, the opponent's best reply line after each, "
        "and the static differences between the two lines' end positions. Start here when "
        "asked why one move is worse than another."
    )
)
async def compare(fen: str, san_a: str, san_b: str, depth: int | None = None) -> CompareResult:
    with _tool_errors():
        return await compare_impl(fen, san_a, san_b, depth)


@mcp.tool(
    description=(
        "Tactical motifs created by playing `san` in `fen` (fork, pin, discovered attack, "
        "hanging piece, back rank, mate threat...)."
    )
)
def motifs(fen: str, san: str) -> MotifsResult:
    with _tool_errors():
        return motifs_impl(fen, san)


@mcp.tool(
    description=(
        "How likely players of a rating are to play each move here (Maia). `include` forces "
        "specific moves into the answer."
    )
)
def maia_probs(
    fen: str, rating: int | None = None, include: list[str] | str | None = None
) -> ProbsResult:
    with _tool_errors():
        return maia_probs_impl(fen, rating, include)


@mcp.tool(
    description=(
        "Pawn-structure classification and static features (king safety, activity, space, "
        "weaknesses, passed pawns) of both sides."
    )
)
def features(fen: str) -> FeaturesResult:
    with _tool_errors():
        return features_impl(fen)
