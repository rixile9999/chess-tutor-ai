"""Everything under /openings: the user's own map, the position guide, the move commentary,
the deep notes and the "더 깊이" lookups. One router, in the order the page uses them.

  GET  /openings/map        overlay DAG over the user's games      (openings_map)
  GET  /openings/heatmap    piece destination heatmap              (openings_map)
  GET  /openings/breaks     pawn break timing                      (openings_map)
  GET  /openings/position   book candidates, structure, setups     (opening_guide)
  POST /openings/annotate   what every move of a line does         (opening_intent)
  GET  /openings/note       the deep note for one move, or missing (opening_notes)
  POST /openings/note       write that note with Claude Code       (opening_notes)
  POST /openings/note/stream  the same, stage by stage and section by section (M8d-2)
  POST /openings/note/addendum  keep one tutor answer on the note  (M8d-3)
  GET  /openings/lines      engine continuations, cached           (opening_deeper)
  GET  /openings/masters    Lichess master statistics              (opening_deeper)

The map endpoints read the user's games (models.User join) played with the requested colour and
do their chess work in a worker thread so PGN parsing never blocks the event loop. The guide and
the commentary are stateless; the notes are stored per (position key, move, language) and are
the only endpoints here that write to the database.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session, session_factory
from chess_tutor.engine import EngineBusy
from chess_tutor.models import Game, User
from chess_tutor.schemas import (
    AddendumRequest,
    AnnotateRequest,
    AnnotateResponse,
    BreakTiming,
    Color,
    DeeperLines,
    MasterStats,
    NoteMissing,
    NoteRequest,
    OpeningMap,
    OpeningNote,
    PieceHeatmap,
    PositionGuide,
)
from chess_tutor.services import opening_deeper, opening_guide, opening_intent, opening_notes
from chess_tutor.services.openings_map import (
    COLOR_NAMES_KO,
    PRACTICE_SOURCE,
    break_timing,
    build_map,
    piece_heatmap,
)

router = APIRouter(prefix="/openings", tags=["openings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
IncludePractice = Annotated[bool, Query(description="연습 게임(source=practice)도 포함할지")]

ENGINE_TROUBLE = "엔진을 사용할 수 없습니다. 엔진 없이 보려면 engine=off로 요청해 주세요."
"""No binary, a killed process or a pool with nothing free - every one of them a RuntimeError,
answered with 503 the way routers/play.py answers it."""
NO_ENGINE = "엔진을 찾을 수 없습니다. STOCKFISH_PATH를 확인해 주세요."
ENGINE_DIED = "엔진이 분석 도중 종료됐습니다. 국면을 확인한 뒤 다시 시도해 주세요."


@router.get("/_status")
def status() -> dict[str, str]:
    return {"module": "openings", "status": "ready"}


async def _user_games(
    session: AsyncSession,
    username: str,
    color: Color,
    platform: str | None,
    include_practice: bool = False,
) -> list[Game]:
    stmt = (
        select(Game)
        .join(User, Game.user_id == User.id)
        .where(User.username == username, Game.user_color == color)
        .order_by(Game.played_at, Game.id)
    )
    if platform:
        stmt = stmt.where(User.platform == platform)
    if not include_practice:
        stmt = stmt.where(Game.source != PRACTICE_SOURCE)
    games = list((await session.execute(stmt)).scalars().all())
    if not games:
        raise HTTPException(
            status_code=404,
            detail=f"{username}의 {COLOR_NAMES_KO[color]} 기보가 없습니다. 먼저 기보를 가져오세요.",
        )
    return games


@router.get("/map", response_model=OpeningMap)
async def opening_map(
    session: SessionDep,
    username: str,
    color: Color,
    depth: Annotated[int, Query(ge=1, le=40, description="플라이 단위 깊이")] = 12,
    min_games: Annotated[int, Query(ge=1)] = 2,
    platform: str | None = None,
    include_practice: IncludePractice = False,
) -> OpeningMap:
    games = await _user_games(session, username, color, platform, include_practice)
    return await asyncio.to_thread(build_map, games, color, depth, min_games)


@router.get("/heatmap", response_model=PieceHeatmap)
async def heatmap(
    session: SessionDep,
    username: str,
    color: Color,
    piece: Annotated[str, Query(min_length=3, max_length=3, description="예: bf8")],
    through_move: Annotated[int, Query(ge=1, le=60)] = 15,
    platform: str | None = None,
    include_practice: IncludePractice = False,
) -> PieceHeatmap:
    games = await _user_games(session, username, color, platform, include_practice)
    try:
        return await asyncio.to_thread(piece_heatmap, games, color, piece, through_move)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/breaks", response_model=list[BreakTiming])
async def breaks(
    session: SessionDep,
    username: str,
    color: Color,
    structure: str | None = None,
    platform: str | None = None,
    include_practice: IncludePractice = False,
) -> list[BreakTiming]:
    games = await _user_games(session, username, color, platform, include_practice)
    return await asyncio.to_thread(break_timing, games, color, structure)


# ---------- 국면 안내와 해설 (M8a~M8d) ----------


@router.get("/position", response_model=PositionGuide)
async def position(
    fen: Annotated[str, Query(min_length=10, description="국면 FEN")],
    color: Annotated[Color, Query(description="내 색. 마스터 승률을 이 쪽에서 봅니다")] = "white",
    masters: Annotated[bool, Query(description="마스터 통계를 겹칠지(토큰이 있을 때만)")] = False,
) -> PositionGuide:
    """Next named positions, the pawn structure and every setup's progress.

    The book work is a few milliseconds; only the master overlay touches the network, and it
    runs in a worker thread so its three-second timeout never blocks the event loop."""
    try:
        return await asyncio.to_thread(opening_guide.guide, fen, color, masters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/annotate", response_model=AnnotateResponse)
async def annotate(req: AnnotateRequest) -> AnnotateResponse:
    """What every move of the line does, one entry per move, in the journal's order.

    Deterministic detectors only, unless the request asks for Maia (`naturalness`) or the
    engine (`engine`); the engine runs for at most opening_intent.ANNOTATE_ENGINE_CAP moves and
    both block, so the whole replay happens in a worker thread."""
    try:
        annotations = await asyncio.to_thread(opening_intent.annotate, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=ENGINE_TROUBLE) from exc
    return AnnotateResponse(annotations=annotations)


@router.get("/note", response_model=OpeningNote | NoteMissing)
async def note(
    session: SessionDep,
    fen: Annotated[str, Query(min_length=10, description="수를 두기 전 국면 FEN")],
    san: Annotated[str, Query(min_length=2, description="그 국면에서 둔 수")],
) -> OpeningNote | NoteMissing:
    """The stored deep note for this move, or `{"status": "missing"}` when none was written."""
    try:
        stored = await opening_notes.load(session, fen, san)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return stored if stored is not None else NoteMissing()


@router.post("/note", response_model=OpeningNote)
async def write_note(req: NoteRequest, session: SessionDep) -> OpeningNote:
    """Write the deep note for one move with Claude Code, verify it, and store it.

    Returns the stored note untouched when there is one, unless `regenerate` is set. Takes
    about ten seconds, which is why the page shows what it is doing meanwhile (plan §9.2);
    503 when Claude Code is not installed or could not answer, and nothing is stored then."""
    try:
        return await opening_notes.write(session, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except opening_notes.NoteUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """One event per frame, exactly as the chat sends them (routers/chat)."""
    async for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/note/stream")
async def stream_note(req: NoteRequest, session: SessionDep) -> StreamingResponse:
    """Write the note and report it while it is being written (plan §10.2).

    Events: `stage` per step of the facts block, `section` per part of the note (the server's
    `mine` and `engine` first, then the model's as each NDJSON line arrives, each already
    verified), `tool` for every tool the model calls, a final `note` with the stored note, and
    `warning`/`error`. Closing the connection kills the Claude Code process and stores nothing.

    The FEN and the move are checked here, while the request's own session is still open, so a
    bad one is still a 422; the stream itself runs on a session of its own because the
    request-scoped one is closed as soon as this function returns."""
    try:
        await opening_notes.load(session, req.fen, req.san)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def events() -> AsyncIterator[dict[str, Any]]:
        async with session_factory()() as own:
            async for event in opening_notes.stream(own, req):
                yield event

    return StreamingResponse(
        _sse(events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/note/addendum", response_model=OpeningNote)
async def note_addendum(req: AddendumRequest, session: SessionDep) -> OpeningNote:
    """Keep one tutor answer on this move's note ("해설에 반영", plan §10.3).

    The answer is stored as the chat showed it — its unverified squares included — and comes
    back in `GET /openings/note` as `addenda`."""
    try:
        return await opening_notes.add_addendum(session, req)
    except ValueError as exc:
        status = 404 if str(exc) == opening_notes.NO_NOTE else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


# ---------- 더 깊이 (M8d-4) ----------


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
