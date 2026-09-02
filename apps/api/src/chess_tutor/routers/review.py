"""review endpoints. Owned by the review workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "review", "status": "stub"}
