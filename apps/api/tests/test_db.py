from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from chess_tutor import db, models
from chess_tutor.models import Game
from chess_tutor.services import puzzles


async def test_insert_and_query_game() -> None:
    async with db.session_factory()() as session:
        session.add(
            Game(source="pgn", source_id="t1", pgn="1. e4 *", white="a", black="b", result="*")
        )
        await session.commit()
    async with db.session_factory()() as session:
        rows = (await session.execute(select(Game))).scalars().all()
    assert len(rows) == 1 and rows[0].source_id == "t1"


async def test_utcnow_is_naive_utc_and_comparable_after_a_round_trip() -> None:
    """`models.utcnow` reads an aware clock but stores naive UTC.

    SQLite keeps no offset, so a timezone-aware default would come back naive and every
    comparison against it (puzzle due dates, profile windows) would raise. Pin both halves:
    the value is naive, and it agrees with the aware clock and with services.puzzles.now_utc.
    """
    before = datetime.now(UTC)
    stamp = models.utcnow()
    after = datetime.now(UTC)

    assert stamp.tzinfo is None
    assert before.replace(tzinfo=None) <= stamp <= after.replace(tzinfo=None)
    assert abs(stamp - puzzles.now_utc()) < timedelta(seconds=5)

    async with db.session_factory()() as session:
        session.add(Game(source="pgn", source_id="stamped", pgn="1. e4 *", white="a", black="b"))
        await session.commit()
    async with db.session_factory()() as session:
        row = (await session.execute(select(Game))).scalars().one()
        # No TypeError here is the point: a stored aware value would make this comparison fail.
        assert row.created_at <= models.utcnow()
        assert row.created_at.tzinfo is None

    # Rows can still be filtered by a timestamp built the same way.
    async with db.session_factory()() as session:
        recent = (
            (
                await session.execute(
                    select(Game).where(Game.created_at >= before.replace(tzinfo=None))
                )
            )
            .scalars()
            .all()
        )
    assert [g.source_id for g in recent] == ["stamped"]
