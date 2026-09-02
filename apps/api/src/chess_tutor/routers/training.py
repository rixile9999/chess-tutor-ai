"""training endpoints. Owned by the training workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/training", tags=["training"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "training", "status": "stub"}
