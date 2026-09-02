"""HTTP API. Thin: every endpoint delegates to a service module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from chess_tutor import __version__
from chess_tutor.db import init_db
from chess_tutor.engine import pool
from chess_tutor.jobs import runner
from chess_tutor.routers import (
    analysis,
    games,
    maia,
    openings,
    positions,
    profile,
    review,
    training,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    runner.start()
    try:
        yield
    finally:
        await runner.stop()
        pool.close()


app = FastAPI(title="chess-tutor", version=__version__, lifespan=lifespan)

for r in (positions, games, analysis, review, profile, openings, training, maia):
    app.include_router(r.router)


@app.exception_handler(OverflowError)
async def _overflow(_: Request, exc: OverflowError) -> JSONResponse:
    """Path or query integers too large for the database (SQLite INTEGER is 64-bit)."""
    return JSONResponse(status_code=422, content={"detail": f"값이 너무 큽니다: {exc}"})


@app.exception_handler(IntegrityError)
async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
    """Safety net for unique-constraint races the services did not catch themselves."""
    return JSONResponse(
        status_code=409,
        content={"detail": "같은 데이터가 이미 저장되어 있습니다.", "cause": str(exc.orig)},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
