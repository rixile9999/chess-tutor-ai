"""Profile endpoints: the weakness report of one user over a window of recent games.

Thin by design; every aggregation lives in services/profile.py."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session
from chess_tutor.schemas import ProfileReport
from chess_tutor.services import profile as profile_svc

router = APIRouter(prefix="/profile", tags=["profile"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
Days = Annotated[int, Query(ge=1, le=3650, description="리포트에 넣을 최근 일수")]

USER_NOT_FOUND = "사용자를 찾을 수 없습니다. 먼저 이 이름으로 게임을 가져오세요."


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "profile", "status": "ok"}


@router.get("/{username}", response_model=ProfileReport)
async def get_profile(username: str, session: SessionDep, days: Days = 60) -> ProfileReport:
    """Weakness report over the user's games of the last ``days`` days (default 60)."""
    try:
        return await profile_svc.build_report(session, username, days=days)
    except profile_svc.UserNotFound as exc:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND) from exc
