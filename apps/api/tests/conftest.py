import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_chess_tutor.db")

from chess_tutor import db  # noqa: E402
from chess_tutor.api import app  # noqa: E402


@pytest.fixture(autouse=True)
async def _fresh_db() -> AsyncIterator[None]:
    """Each test starts from empty tables."""
    await db.reset_engine()
    engine = db.get_engine()
    from chess_tutor import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)
    yield
    await db.reset_engine()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def aclient() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
