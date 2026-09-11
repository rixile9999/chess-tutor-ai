"""Opening map: the position guide (M8a) and the move commentary (M8b, M8d).

  GET  /openings/position   book candidates, pawn structure, setup progress   (opening_guide)
  POST /openings/annotate   what every move of a line does, as verified facts  (opening_intent)
  GET  /openings/note       the deep note for one move, or {"status": "missing"} (opening_notes)
  POST /openings/note       write that note with Claude Code and store it       (opening_notes)
  POST /openings/note/stream  the same, reported stage by stage and section by section (M8d-2)

The first two are stateless; the notes are stored per (position key, move, language) and are
the only endpoints here that read the database. Shares the /openings prefix with
routers/openings.py; the two are merged in M8d.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.db import get_session, session_factory
from chess_tutor.schemas import (
    AnnotateRequest,
    AnnotateResponse,
    Color,
    NoteMissing,
    NoteRequest,
    OpeningNote,
    PositionGuide,
)
from chess_tutor.services import opening_guide, opening_intent, opening_notes

router = APIRouter(prefix="/openings", tags=["openings"])

Session = Annotated[AsyncSession, Depends(get_session)]

ENGINE_TROUBLE = "엔진을 사용할 수 없습니다. 엔진 없이 보려면 engine=off로 요청해 주세요."
"""No binary, a killed process or a pool with nothing free — every one of them a RuntimeError,
answered with 503 the way routers/play.py answers it."""


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
    session: Session,
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
async def write_note(req: NoteRequest, session: Session) -> OpeningNote:
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
async def stream_note(req: NoteRequest, session: Session) -> StreamingResponse:
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
