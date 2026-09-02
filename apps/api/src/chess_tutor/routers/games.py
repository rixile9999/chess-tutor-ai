"""games endpoints. Owned by the games workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/games", tags=["games"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "games", "status": "stub"}
