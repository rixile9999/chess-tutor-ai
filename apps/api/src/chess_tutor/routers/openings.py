"""openings endpoints. Owned by the openings workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/openings", tags=["openings"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "openings", "status": "stub"}
