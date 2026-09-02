from sqlalchemy import select

from chess_tutor import db
from chess_tutor.models import Game


async def test_insert_and_query_game() -> None:
    async with db.session_factory()() as session:
        session.add(
            Game(source="pgn", source_id="t1", pgn="1. e4 *", white="a", black="b", result="*")
        )
        await session.commit()
    async with db.session_factory()() as session:
        rows = (await session.execute(select(Game))).scalars().all()
    assert len(rows) == 1 and rows[0].source_id == "t1"
