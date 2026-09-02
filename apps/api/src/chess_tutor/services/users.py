"""One place that turns a username into accounts.

Every screen has to resolve a name the same way or the numbers stop matching: the profile
counted 'duke' and 'Duke' as one user while training treated them as two, so a profile could
promise puzzles the training screen never showed. Resolution here is case-insensitive,
whitespace-trimmed and spans every platform the name exists on.

Reading a name never creates an account. Accounts are created by an import
(``services.games.get_or_create_user``), so a query parameter cannot mint a second user and
split a deck away from its profile.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from chess_tutor.models import User


class UserNotFound(LookupError):
    """No account carries this name on any platform."""


def normalise(username: str) -> str:
    """The comparison form of a username: trimmed and lowercased."""
    return username.strip().lower()


def matches(username: str) -> ColumnElement[bool]:
    """SQL clause selecting every account with this name, whatever its case or platform."""
    return func.lower(User.username) == normalise(username)


async def find_users(session: AsyncSession, username: str) -> list[User]:
    """Every account with this name, platform accounts before local ones."""
    users = list((await session.execute(select(User).where(matches(username)))).scalars())
    users.sort(key=lambda u: (u.platform == "local", u.id))
    return users


async def require_user(session: AsyncSession, username: str) -> User:
    """The primary account with this name. Raises UserNotFound when the name is unknown."""
    users = await find_users(session, username)
    if not users:
        raise UserNotFound(username)
    return users[0]
