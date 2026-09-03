"""Position chat endpoints. The question is answered by Claude Code headless (services.chat);
the answer streams back as server-sent events: text deltas, tool calls, board states, and a
final `done`. The review of the ply is built (or read from cache) first because the tutor's
system prompt is made of it."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models
from chess_tutor.config import get_settings
from chess_tutor.db import get_session
from chess_tutor.routers.review import NOT_FOUND, PLY_NOT_FOUND, _analysis
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
