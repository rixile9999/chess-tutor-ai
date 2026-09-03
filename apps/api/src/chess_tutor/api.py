"""HTTP API. Thin: every endpoint delegates to a service module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.exc import IntegrityError
from starlette.types import ASGIApp, Receive, Scope, Send

from chess_tutor import __version__
from chess_tutor.config import get_settings
from chess_tutor.db import init_db
from chess_tutor.engine import pool
from chess_tutor.jobs import runner
from chess_tutor.routers import (
    analysis,
    chat,
    games,
    maia,
    openings,
    positions,
    profile,
    review,
    training,
)
from chess_tutor.services import chat_tools


class _MCPMount:
    """ASGI app for /mcp that forwards to the chess-tool MCP server built by the lifespan.

    The MCP session manager can only be run once per instance, and a test client runs the
    lifespan once per test, so the lifespan builds a fresh server app (and manager) every time
    it starts instead of the module building one at import."""

    def __init__(self) -> None:
        self.app: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.app is None:
            await JSONResponse({"detail": "MCP server not started"}, status_code=503)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


mcp_mount = _MCPMount()


def build_mcp_app() -> ASGIApp:
    """The chess tools the position chat exposes to Claude Code, served in-process so they
    share the engine pool, the cache and the database. Stateless: every tool call is one
    request. The server only ever listens on localhost; the loopback hosts are what Claude Code
    sends."""
    return chat_tools.mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(get_settings().chat_mcp_allowed_hosts),
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    runner.start()
    mcp_mount.app = build_mcp_app()
    try:
        async with chat_tools.mcp.session_manager.run():
            yield
    finally:
        mcp_mount.app = None
        await runner.stop()
        pool.close()


app = FastAPI(title="chess-tutor", version=__version__, lifespan=lifespan)

for r in (positions, games, analysis, review, chat, profile, openings, training, maia):
    app.include_router(r.router)

app.mount("/mcp", mcp_mount, name="mcp")


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
