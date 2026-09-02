"""profile endpoints. Owned by the profile workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "profile", "status": "stub"}
