"""오프닝 지도 "더 깊이" (M8d-4, plan §10.4).

  GET /openings/lines     engine continuations from a position (cached in EngineCache)
  GET /openings/masters   Lichess master statistics for the same position

Both are stateless and the page only calls them when the section is opened, so the engine runs
because the user asked for it. Shares the /openings prefix with routers/openings.py and
routers/opening_guide.py; the three are merged in M8d-5.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import chess.engine
from fastapi import APIRouter, HTTPException, Query

from chess_tutor.engine import EngineBusy
from chess_tutor.schemas import DeeperLines, MasterStats
from chess_tutor.services import opening_deeper

router = APIRouter(prefix="/openings", tags=["openings"])

NO_ENGINE = "엔진을 찾을 수 없습니다. STOCKFISH_PATH를 확인해 주세요."
ENGINE_DIED = "엔진이 분석 도중 종료됐습니다. 국면을 확인한 뒤 다시 시도해 주세요."


@router.get("/lines", response_model=DeeperLines)
async def lines(
    fen: Annotated[str, Query(min_length=10, description="분석할 국면 FEN")],
    depth: Annotated[int, Query(ge=1, le=30, description="엔진 탐색 깊이")] = 12,
    multipv: Annotated[int, Query(ge=1, le=5, description="보여 줄 라인 수")] = 3,
) -> DeeperLines:
    """The engine's top lines from this position, best first.

    The search itself happens in `services.analysis.get_lines`, which answers from the engine
    cache when the same (국면, 깊이, 라인 수) was asked for before."""
    try:
        return await opening_deeper.lines(fen, depth, multipv)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except chess.engine.EngineTerminatedError as exc:
        # EngineTerminatedError is an EngineError is a RuntimeError: caught first so a killed
        # process is never reported as a missing binary (routers/analysis.py does the same).
        raise HTTPException(status_code=503, detail=ENGINE_DIED) from exc
    except EngineBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=NO_ENGINE) from exc


@router.get("/masters", response_model=MasterStats)
async def masters(
    fen: Annotated[str, Query(min_length=10, description="국면 FEN")],
) -> MasterStats:
    """Master statistics for this position, busiest move first.

    Never 5xx: without a Lichess token, or with an explorer that did not answer, the body says
    `available: false` and carries the reason the panel shows. The explorer call blocks, so it
    runs in a worker thread with its own three-second timeout."""
    try:
        return await asyncio.to_thread(opening_deeper.masters, fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
