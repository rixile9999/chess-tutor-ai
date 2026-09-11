"""Position chat endpoints. The question is answered by Claude Code headless (services.chat);
the answer streams back as server-sent events: text deltas, tool calls, board states, and a
final `done`. The review of the ply is built (or read from cache) first because the tutor's
system prompt is made of it."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import chess
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models
from chess_tutor.config import get_settings
from chess_tutor.db import get_session
from chess_tutor.routers.review import NOT_FOUND, PLY_NOT_FOUND, _analysis
from chess_tutor.schemas import Color, OpeningContext
from chess_tutor.services import chat as chat_svc
from chess_tutor.services import chat_prompt
from chess_tutor.services import review as review_svc

router = APIRouter(tags=["chat"])

Session = Annotated[AsyncSession, Depends(get_session)]
Depth = Annotated[int | None, Query(ge=1, le=40, description="분석 깊이. 비우면 설정값")]
Rating = Annotated[int | None, Query(ge=400, le=3200, description="Maia 레이팅. 비우면 설정값")]


class MoveAttachment(BaseModel):
    fen: str = Field(description="보드에 있던 국면")
    san: str = Field(description="학생이 그 국면에서 둔 수")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    move: MoveAttachment | None = None


class LiveChatRequest(ChatRequest):
    """A question about a practice-game position that is not stored yet (M7)."""

    fen: str = Field(description="지금 보드에 있는 국면")
    start_fen: str = Field(default=chess.STARTING_FEN, description="게임의 시작 국면")
    moves_san: list[str] = Field(default_factory=list, description="시작 국면부터 지금까지의 수")
    user_color: Color | None = Field(default=None, description="학생의 색. 수동 게임이면 비움")
    opponent: str | None = Field(default=None, description='상대 표시 이름, 예: "Maia 1500"')
    opening_name: str | None = None
    opening: OpeningContext | None = Field(
        default=None, description="오프닝 지도에서 읽고 있는 해설. 있으면 대화가 그 수에 붙는다"
    )


def _session_key(req: LiveChatRequest) -> str:
    """What one live conversation is about. A question from the opening map is about a *move*,
    so the key is the position it was played in plus the move: the same position with another
    move is another conversation (plan §10.3). Otherwise it is the position on the board."""
    if req.opening is not None:
        return f"{req.opening.fen_before}|{req.opening.san}"
    return req.fen


class ChatStatus(BaseModel):
    available: bool
    command: str
    model: str
    reason: str | None = None


@router.get("/chat/status", response_model=ChatStatus)
def status() -> ChatStatus:
    """Whether the Claude Code binary the chat shells out to can be found."""
    return ChatStatus.model_validate(chat_svc.availability())


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/review/{game_id}/{ply}/chat")
async def chat(
    game_id: int,
    ply: int,
    req: ChatRequest,
    session: Session,
    rating: Rating = None,
    depth: Depth = None,
) -> StreamingResponse:
    """Ask the tutor about this move. Pass the `session_id` from the first event to continue
    the same conversation; a session belongs to one (game, ply)."""
    game = await session.get(models.Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    analysis = await _analysis(game_id, depth)
    if not 1 <= ply <= len(analysis.moves):
        raise HTTPException(status_code=404, detail=PLY_NOT_FOUND)
    r = rating or get_settings().default_rating
    chat_session = chat_svc.get_session(req.session_id)
    if chat_session is None or chat_session.game_id != game_id or chat_session.ply != ply:
        review = await review_svc.build_move_review(session, game, analysis, ply, r)
        prompt = chat_prompt.build_system_prompt(game, analysis, review, r)
        chat_session = chat_svc.create_session(game_id, ply, prompt)
    if chat_session.lock.locked():
        # Fast path; run_turn re-checks under the lock and reports the same thing as an event.
        raise HTTPException(status_code=409, detail="이 대화는 아직 이전 답을 쓰는 중입니다.")
    move_fen = req.move.fen if req.move else None
    move_san = req.move.san if req.move else None
    return StreamingResponse(
        _sse(chat_svc.run_turn(chat_session, req.message, move_fen, move_san)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/play/chat")
async def live_chat(req: LiveChatRequest, rating: Rating = None) -> StreamingResponse:
    """Ask the tutor about the position of a practice game in progress, or about the move an
    opening note explains. The session follows what the question is about (`_session_key`): a
    question from a different position — or about a different move — starts a new conversation."""
    try:
        board = chess.Board(req.fen)
        chess.Board(req.start_fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"읽을 수 없는 FEN: {exc}") from exc
    if not board.is_valid():
        raise HTTPException(status_code=422, detail="규칙에 맞지 않는 국면입니다.")
    r = rating or get_settings().default_rating
    key = _session_key(req)
    chat_session = chat_svc.get_session(req.session_id)
    if chat_session is None or chat_session.game_id is not None or chat_session.fen != key:
        prompt = chat_prompt.build_live_prompt(
            req.fen,
            req.start_fen,
            req.moves_san,
            req.user_color,
            r,
            req.opponent,
            req.opening_name,
            req.opening,
        )
        chat_session = chat_svc.create_session(None, len(req.moves_san), prompt, fen=key)
    if chat_session.lock.locked():
        raise HTTPException(status_code=409, detail="이 대화는 아직 이전 답을 쓰는 중입니다.")
    move_fen = req.move.fen if req.move else None
    move_san = req.move.san if req.move else None
    return StreamingResponse(
        _sse(chat_svc.run_turn(chat_session, req.message, move_fen, move_san)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
