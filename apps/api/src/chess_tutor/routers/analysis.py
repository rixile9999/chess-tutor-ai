"""analysis endpoints. Owned by the analysis workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "analysis", "status": "stub"}
