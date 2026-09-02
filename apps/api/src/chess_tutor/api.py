"""HTTP API. Thin: every endpoint delegates to a service module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from chess_tutor import __version__
from chess_tutor.db import init_db
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


app = FastAPI(title="chess-tutor", version=__version__, lifespan=lifespan)

for r in (positions, games, analysis, review, profile, openings, training, maia):
    app.include_router(r.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
