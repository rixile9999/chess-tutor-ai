"""Fetch a player's games from chess.com and lichess (layer 1 data sources).

Network only: the PGN each platform returns goes through services.games.parse_pgn, and the
platform's own game id replaces the sha1 as source_id so re-syncs never duplicate a game.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor.config import get_settings
from chess_tutor.schemas import ImportResult
from chess_tutor.services.games import (
    ParsedGame,
    parse_pgn,
    set_user_ratings,
    upsert_games,
    utc_from_epoch,
)

log = logging.getLogger(__name__)

CHESSCOM_API = "https://api.chess.com/pub"
LICHESS_API = "https://lichess.org/api"
_LICHESS_ID = re.compile(r"lichess\.org/([A-Za-z0-9]{8})")

Ratings = dict[str, int | None]


class FetchError(Exception):
    """A platform request failed; the message is user-facing Korean."""


def _client(token: str | None = None, read_timeout: float = 60.0) -> httpx.AsyncClient:
    headers = {"User-Agent": get_settings().chesscom_user_agent}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = httpx.Timeout(connect=10.0, read=read_timeout, write=10.0, pool=10.0)
    return httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True)


def _check(response: httpx.Response, platform: str, username: str) -> None:
    if response.status_code == 404:
        raise FetchError(f"{platform}에서 '{username}' 계정을 찾지 못했습니다.")
    if response.status_code == 429:
        raise FetchError(f"{platform} 요청 한도를 넘었습니다. 잠시 후 다시 시도해 주세요.")
    if response.status_code >= 400:
        raise FetchError(f"{platform} 응답이 올바르지 않습니다 (HTTP {response.status_code}).")


async def _get(
    client: httpx.AsyncClient, url: str, platform: str, username: str, **kw: Any
) -> httpx.Response:
    try:
        response = await client.get(url, **kw)
    except httpx.HTTPError as exc:
        raise FetchError(f"{platform}에 연결하지 못했습니다: {exc}") from exc
    _check(response, platform, username)
    return response


# ---------- chess.com ----------


def _chesscom_entry(entry: dict[str, Any]) -> ParsedGame | None:
    """One games[] item from a monthly archive. Variants and unreadable PGNs are dropped."""
    if entry.get("rules", "chess") != "chess":
        return None
    pgn = entry.get("pgn")
    if not pgn:
        return None
    report = parse_pgn(pgn)
    if not report.games:
        log.warning("chess.com game %s skipped: %s", entry.get("url"), report.errors)
        return None
    game = report.games[0]
    url = entry.get("url")
    if url:
        game.source_id = url.rstrip("/").rsplit("/", 1)[-1]
        game.headers.setdefault("Link", url)
    white, black = entry.get("white") or {}, entry.get("black") or {}
    game.white = white.get("username") or game.white
    game.black = black.get("username") or game.black
    if isinstance(white.get("rating"), int):
        game.white_elo = white["rating"]
    if isinstance(black.get("rating"), int):
        game.black_elo = black["rating"]
    if game.time_control is None and entry.get("time_control"):
        game.time_control = str(entry["time_control"])
    if game.played_at is None:
        game.played_at = utc_from_epoch(entry.get("end_time"))
    if entry.get("time_class"):
        game.headers["TimeClass"] = str(entry["time_class"])
    return game


async def chesscom_fetch(username: str, months: int = 3) -> list[ParsedGame]:
    """Games from the player's last `months` monthly archives, oldest archive first."""
    user = username.strip().lower()
    games: list[ParsedGame] = []
    async with _client() as client:
        res = await _get(client, f"{CHESSCOM_API}/player/{user}/games/archives", "chess.com", user)
        archives: list[str] = res.json().get("archives", [])
        for url in archives[-months:]:
            res = await _get(client, url, "chess.com", user)
            for entry in res.json().get("games", []):
                game = _chesscom_entry(entry)
                if game is not None:
                    games.append(game)
    return games


def _rating(perf: Any) -> int | None:
    if not isinstance(perf, dict):
        return None
    last = perf.get("last") if "last" in perf else perf
    rating = last.get("rating") if isinstance(last, dict) else None
    return rating if isinstance(rating, int) else None


async def chesscom_ratings(username: str) -> Ratings:
    """{'rapid': ..., 'blitz': ...} from /pub/player/{u}/stats (chess_rapid.last.rating)."""
    user = username.strip().lower()
    async with _client() as client:
        res = await _get(client, f"{CHESSCOM_API}/player/{user}/stats", "chess.com", user)
    stats = res.json()
    return {"rapid": _rating(stats.get("chess_rapid")), "blitz": _rating(stats.get("chess_blitz"))}


# ---------- lichess ----------


async def lichess_fetch(
    username: str, max_games: int = 100, token: str | None = None
) -> list[ParsedGame]:
    """Most recent games as PGN with clocks and opening names. token raises the rate limit."""
    user = username.strip()
    params = {"max": max_games, "clocks": "true", "opening": "true"}
    async with _client(token, read_timeout=600.0) as client:
        res = await _get(
            client,
            f"{LICHESS_API}/games/user/{user}",
            "lichess",
            user,
            params=params,
            headers={"Accept": "application/x-chess-pgn"},
        )
    report = parse_pgn(res.text)
    for error in report.errors:
        log.info("lichess export for %s: %s", user, error)
    for game in report.games:
        match = _LICHESS_ID.search(game.headers.get("Site", ""))
        if match:
            game.source_id = match.group(1)
    return report.games


async def lichess_ratings(username: str, token: str | None = None) -> Ratings:
    user = username.strip()
    async with _client(token) as client:
        res = await _get(client, f"{LICHESS_API}/user/{user}", "lichess", user)
    perfs = res.json().get("perfs") or {}
    return {"rapid": _rating(perfs.get("rapid")), "blitz": _rating(perfs.get("blitz"))}


# ---------- import flows (fetch -> store -> ratings) ----------


async def _store_ratings(
    session: AsyncSession, user_id: int | None, fetch: Callable[[], Awaitable[Ratings]]
) -> None:
    if user_id is None:
        return
    try:
        ratings = await fetch()
    except FetchError as exc:
        log.warning("ratings not stored: %s", exc)
        return
    await set_user_ratings(session, user_id, ratings.get("rapid"), ratings.get("blitz"))


async def import_chesscom(session: AsyncSession, username: str, months: int = 3) -> ImportResult:
    games = await chesscom_fetch(username, months)
    result = await upsert_games(session, games, source="chesscom", username=username)
    await _store_ratings(session, result.user_id, lambda: chesscom_ratings(username))
    return result


async def import_lichess(
    session: AsyncSession, username: str, max_games: int = 100, token: str | None = None
) -> ImportResult:
    token = token or get_settings().lichess_token
    games = await lichess_fetch(username, max_games, token)
    result = await upsert_games(session, games, source="lichess", username=username)
    await _store_ratings(session, result.user_id, lambda: lichess_ratings(username, token))
    return result
