"""maia endpoints. Owned by the maia workstream; see docs/IMPLEMENTATION.md."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/maia", tags=["maia"])


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "maia", "status": "stub"}
