"""Schema drift that create_all cannot repair.

The project has no Alembic yet: tables are created once and never altered. A column whose
nullability changed after a database was made therefore needs a hand-written step, and this is
where those steps are proved to run.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa

from chess_tutor import db
from chess_tutor.config import get_settings

PRE_M7_CHAT_TURNS = """
CREATE TABLE chat_turns (
    id INTEGER NOT NULL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    game_id INTEGER NOT NULL,
    ply INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL,
    content JSON,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(game_id) REFERENCES games (id)
)
"""
"""chat_turns as it was before /play/chat: a turn always belonged to a stored game."""


@pytest.fixture
async def pre_m7_database() -> AsyncIterator[str]:
    """A database file whose chat_turns still refuses a NULL game_id, with one row in it."""
    path = os.path.join(tempfile.mkdtemp(prefix="chess-tutor-premigration-"), "old.db")
    sync = sa.create_engine(f"sqlite:///{path}")
    with sync.begin() as conn:
        conn.exec_driver_sql(PRE_M7_CHAT_TURNS)
        conn.exec_driver_sql("CREATE INDEX ix_chat_turns_session_id ON chat_turns (session_id)")
        conn.exec_driver_sql(
            "INSERT INTO chat_turns (id, session_id, game_id, ply, role, content, created_at)"
            " VALUES (1, 'old-session', 7, 3, 'user', '{}', '2026-01-01 00:00:00')"
        )
    sync.dispose()
    settings = get_settings()
    previous = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{path}"
    await db.reset_engine()
    yield path
    await db.reset_engine()
    settings.database_url = previous


async def test_init_db_lets_a_live_chat_turn_have_no_game(pre_m7_database: str) -> None:
    """M7's live chat stores turns with game_id NULL; the old table would refuse them, and
    services.chat only logs that failure, so the turn would vanish."""
    await db.init_db()
    async with db.session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO chat_turns (session_id, game_id, ply, role, content, created_at)"
                " VALUES ('live', NULL, 0, 'user', '{}', '2026-02-02 00:00:00')"
            )
        )
        await session.commit()
        rows = (await session.execute(sa.text("SELECT session_id, game_id FROM chat_turns"))).all()
    # The turns that were already there are kept, and the index comes back with the table.
    assert sorted(rows) == [("live", None), ("old-session", 7)]
    async with db.get_engine().begin() as conn:
        indexes = await conn.run_sync(
            lambda sync: sa.inspect(sync).get_indexes("chat_turns")  # type: ignore[no-any-return]
        )
    assert [i["name"] for i in indexes] == ["ix_chat_turns_session_id"]


async def test_init_db_is_a_no_op_on_a_current_database() -> None:
    """Running it twice on a database create_all just made must change nothing."""
    await db.init_db()
    await db.init_db()
    async with db.session_factory()() as session:
        assert (await session.execute(sa.text("SELECT count(*) FROM chat_turns"))).scalar() == 0
