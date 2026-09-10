"""Curated opening catalogue for practice: ~30 tabiyas resolved from the bundled lichess TSV.

TODO(M7b): implement. Contract:
  CATALOG: list[CatalogEntry(id, family, name_ko, eco, tsv_name, sides)] hand-written; the
    move line and tabiya FEN come from the TSV row whose (eco, name) matches tsv_name exactly,
    so a typo fails a test instead of shipping. Families: e4e5, sicilian, e4other, d4d5,
    indian, flank (labels in Korean).
  cards(session, username) -> list[OpeningCard]
    structure = structure.classify(tabiya board); record from the user's imported games that
    reach the tabiya position key (openings.position_key) and from practice games whose
    headers["OpeningId"] == id.
  detail(session, id, username) -> OpeningDetail | None
    card + plans_white/plans_black = plans.match_plans(structure.key, side, [], board, [])
    (status "later"/"unavailable" only, no engine) + fens after each move.
  book_moves(fen) -> BookMoves
    Next moves known from this position: built once from every TSV line (each prefix position
    -> the move that follows, keyed by openings.position_key so transpositions merge). Raises
    ValueError for a bad FEN.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.schemas import BookMoves, OpeningCard, OpeningDetail


async def cards(session: AsyncSession, username: str | None) -> list[OpeningCard]:
    raise NotImplementedError("openings_catalog.cards")


async def detail(
    session: AsyncSession, opening_id: str, username: str | None
) -> OpeningDetail | None:
    raise NotImplementedError("openings_catalog.detail")


def book_moves(fen: str) -> BookMoves:
    raise NotImplementedError("openings_catalog.book_moves")
