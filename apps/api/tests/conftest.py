import os
import tempfile
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

# One database file per test process so parallel pytest runs never share state.
_TEST_DB = os.path.join(tempfile.mkdtemp(prefix="chess-tutor-test-"), f"test_{os.getpid()}.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"

# Stockfish's search is only reproducible single-threaded: with the production default of two
# threads the move ordering below rank 1 changes between runs, which made engine tests that read
# the second or third line flaky. Set before the settings object exists so every engine process
# this test session starts is pinned; the fixture below covers a settings object built earlier.
os.environ["ENGINE_THREADS"] = "1"

from chess_tutor import db  # noqa: E402
from chess_tutor import engine as engine_mod  # noqa: E402
from chess_tutor.api import app  # noqa: E402
from chess_tutor.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _deterministic_engine() -> Iterator[None]:
    """One engine thread for the whole session, so multipv ranks 2+ are reproducible."""
    settings = get_settings()
    previous = settings.engine_threads
    settings.engine_threads = 1
    engine_mod.pool.close()  # discard any process started with the previous setting
    yield
    engine_mod.pool.close()
    settings.engine_threads = previous


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


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Quit every engine process, then guarantee the interpreter exits.

    python-chess runs each engine on a non-daemon thread; one that is still alive after the
    session would hang the process forever (seen on the GitHub runner). If anything is still
    blocking a minute later, dump every thread's stack to stderr and exit non-zero so the cause
    shows up in the log instead of a timeout."""
    import faulthandler
    import sys

    engine_mod.close_all()
    faulthandler.dump_traceback_later(60, exit=True, file=sys.stderr)
