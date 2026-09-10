"""Async SQLAlchemy setup. SQLite by default; Postgres via DATABASE_URL."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from chess_tutor.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict[str, object] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_async_engine(url, **kwargs)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def _allow_live_chat_turns(connection: Connection) -> None:
    """Let ``chat_turns.game_id`` be NULL on a database made before M7.

    A live practice position has no stored game, so the column became nullable when
    ``POST /play/chat`` arrived. ``create_all`` never alters a table that already exists, and
    services.chat.store_turn logs the failure instead of raising, so on an older database every
    live chat turn was dropped without a word. SQLite cannot drop a NOT NULL in place, hence the
    rebuild; anything else does it in one statement."""
    from chess_tutor.models import ChatTurn

    inspector = inspect(connection)
    if ChatTurn.__tablename__ not in inspector.get_table_names():
        return
    columns = {c["name"]: c for c in inspector.get_columns(ChatTurn.__tablename__)}
    column = columns.get("game_id")
    if column is None or column["nullable"]:
        return
    if connection.dialect.name != "sqlite":
        connection.exec_driver_sql(
            f"ALTER TABLE {ChatTurn.__tablename__} ALTER COLUMN game_id DROP NOT NULL"
        )
        return
    table = Base.metadata.tables[ChatTurn.__tablename__]
    names = ", ".join(c.name for c in table.columns)
    old = f"{ChatTurn.__tablename__}_pre_m7"
    for index in inspector.get_indexes(ChatTurn.__tablename__):
        connection.exec_driver_sql(f'DROP INDEX IF EXISTS "{index["name"]}"')
    connection.exec_driver_sql(f"ALTER TABLE {ChatTurn.__tablename__} RENAME TO {old}")
    table.create(connection)
    connection.exec_driver_sql(
        f"INSERT INTO {ChatTurn.__tablename__} ({names}) SELECT {names} FROM {old}"
    )
    connection.exec_driver_sql(f"DROP TABLE {old}")


async def init_db() -> None:
    """Create tables. Alembic migrations come later; create_all is enough while the schema moves."""
    from chess_tutor import models  # noqa: F401  (register tables)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_allow_live_chat_turns)


async def reset_engine() -> None:
    """Dispose the engine so a test can point DATABASE_URL somewhere else."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session
