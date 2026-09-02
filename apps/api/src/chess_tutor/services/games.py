"""Game storage: PGN parsing, dedupe on import, listing and the schema views.

Parsing is python-chess only (layer 0). Every move the API reports is replayed on a board, so
san/uci/fen_after are consistent with the rules, and clocks come from %clk comments verbatim.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import chess
import chess.pgn
from sqlalchemy import Select, delete, func, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chess_tutor import openings
from chess_tutor.models import Analysis, Game, MoveReview, Puzzle, User
from chess_tutor.schemas import GameDetail, GameSummary, ImportResult, MoveInfo

log = logging.getLogger(__name__)

STANDARD_START = chess.STARTING_FEN
SUPPORTED_VARIANTS = {"standard", "from position", "chess"}
_PLATFORMS = {"chesscom", "lichess"}
_MISSING = {"", "?", "-", "??"}
PGN_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}
"""The four results the PGN standard defines; anything else is stored as unknown."""
NULL_MOVE_UCI = "0000"

# Header values are written by whoever produced the PGN, and the columns in models.py are
# narrow. SQLite ignores the widths, Postgres raises, so they are cut here instead.
NAME_LEN = 64
TIME_CONTROL_LEN = 32
ECO_LEN = 8
OPENING_LEN = 128


@dataclass
class ParsedGame:
    """One game read from PGN text, ready to be stored or inspected."""

    pgn: str
    """Raw PGN of this game only (headers + movetext), as found in the input."""
    white: str = "?"
    black: str = "?"
    result: str = "*"
    white_elo: int | None = None
    black_elo: int | None = None
    time_control: str | None = None
    played_at: datetime | None = None
    """Naive UTC start time from UTCDate/UTCTime, else Date."""
    eco: str | None = None
    opening_name: str | None = None
    initial_fen: str = STANDARD_START
    moves: list[MoveInfo] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    source_id: str | None = None
    """sha1 of the normalised PGN; importers replace it with the platform's game id."""

    @property
    def ply_count(self) -> int:
        return len(self.moves)

    def color_of(self, username: str | None) -> str | None:
        """'white' or 'black' when username plays in this game (case-insensitive)."""
        if not username:
            return None
        name = username.strip().lower()
        if self.white.lower() == name:
            return "white"
        if self.black.lower() == name:
            return "black"
        return None


@dataclass
class ParseReport:
    games: list[ParsedGame]
    errors: list[str]
    """One Korean sentence per game that was skipped, with its 1-based index in the text."""


# ---------- parsing ----------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return None if value in _MISSING else value


def _fit(value: str | None, limit: int) -> str | None:
    """Trim an untrusted header value to the width of the column that will hold it."""
    return None if value is None else value[:limit]


def _int_or_none(value: str | None) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_played_at(headers: dict[str, str]) -> datetime | None:
    """UTCDate+UTCTime, else Date(+UTCTime). Unknown parts ('??') give None."""
    date = _clean(headers.get("UTCDate")) or _clean(headers.get("Date"))
    if date is None or "?" in date:
        return None
    time = _clean(headers.get("UTCTime")) or "00:00:00"
    if "?" in time:
        time = "00:00:00"
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date} {time}", fmt)
        except ValueError:
            continue
    return None


def pgn_hash(raw: str) -> str:
    """sha1 of headers + movetext with whitespace collapsed, so re-exports still match."""
    normalised = re.sub(r"\s+", " ", raw).strip()
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()


def _walk(game: chess.pgn.Game) -> tuple[chess.Board, list[MoveInfo], list[chess.Move]]:
    """Replay the mainline; returns the start board, MoveInfo per ply, and the raw moves."""
    start = game.board()
    board = start.copy()
    infos: list[MoveInfo] = []
    moves: list[chess.Move] = []
    for node in game.mainline():
        move = node.move
        san = board.san(move)
        board.push(move)
        moves.append(move)
        infos.append(
            MoveInfo(
                ply=len(infos) + 1,
                san=san,
                uci=move.uci(),
                fen_after=board.fen(),
                clock=node.clock(),
            )
        )
    return start, infos, moves


def _from_game(game: chess.pgn.Game, raw: str) -> ParsedGame:
    headers = {k: v for k, v in game.headers.items()}
    start, infos, moves = _walk(game)
    eco = _clean(headers.get("ECO"))
    name = _clean(headers.get("Opening"))
    if eco is None or name is None:
        book, _ = openings.classify_game(moves, start=start)
        if book is not None:
            eco = eco or book.eco
            name = name or book.name
    result = _clean(headers.get("Result")) or "*"
    return ParsedGame(
        pgn=raw,
        white=_fit(_clean(headers.get("White")), NAME_LEN) or "?",
        black=_fit(_clean(headers.get("Black")), NAME_LEN) or "?",
        result=result if result in PGN_RESULTS else "*",
        white_elo=_int_or_none(headers.get("WhiteElo")),
        black_elo=_int_or_none(headers.get("BlackElo")),
        time_control=_fit(_clean(headers.get("TimeControl")), TIME_CONTROL_LEN),
        played_at=parse_played_at(headers),
        eco=_fit(eco, ECO_LEN),
        opening_name=_fit(name, OPENING_LEN),
        initial_fen=start.fen(),
        moves=infos,
        headers=headers,
        source_id=pgn_hash(raw),
    )


def parse_pgn(text: str) -> ParseReport:
    """Read every game in text. Games with illegal moves, no moves, an illegal starting
    position or an unsupported variant are reported in errors and left out, so nothing
    half-parsed reaches the database."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    stream = io.StringIO(text)
    games: list[ParsedGame] = []
    errors: list[str] = []
    index = 0
    while True:
        start = stream.tell()
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        index += 1
        raw = text[start : stream.tell()].strip()
        variant = (game.headers.get("Variant") or "Standard").strip()
        if variant.lower() not in SUPPORTED_VARIANTS:
            errors.append(f"{index}번째 게임은 지원하지 않는 변형 게임이라 건너뜁니다 ({variant}).")
            continue
        if game.errors:
            errors.append(f"{index}번째 게임의 수를 읽을 수 없어 건너뜁니다 ({game.errors[0]}).")
            continue
        try:
            parsed = _from_game(game, raw)
        except ValueError as exc:
            errors.append(f"{index}번째 게임의 시작 국면을 읽을 수 없어 건너뜁니다 ({exc}).")
            continue
        # A syntactically fine [FEN] header can still describe a position the rules forbid
        # (two white kings, no black king, a side already in check on the opponent's move).
        # Stockfish crashes on those, so they must never be stored.
        if not chess.Board(parsed.initial_fen).is_valid():
            errors.append(f"{index}번째 게임의 시작 국면이 규칙에 맞지 않아 건너뜁니다.")
            continue
        if not parsed.moves:
            errors.append(f"{index}번째 게임에는 수가 없어 건너뜁니다.")
            continue
        # A null move ('--') is a placeholder for a move nobody made. Stored as uci 0000 it
        # would be replayed, analysed and reviewed as if it were real.
        if any(move.uci == NULL_MOVE_UCI for move in parsed.moves):
            errors.append(f"{index}번째 게임에는 널 무브(--)가 있어 건너뜁니다.")
            continue
        games.append(parsed)
    return ParseReport(games=games, errors=errors)


def parse_pgn_games(text: str) -> list[ParsedGame]:
    """Every readable game in text (see parse_pgn for what gets dropped)."""
    return parse_pgn(text).games


def _read_stored(game: Game) -> chess.pgn.Game | None:
    """The stored PGN was validated on import, so a None here means a foreign row."""
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    if parsed is None or parsed.errors:
        return None
    return parsed


def game_moves(game: Game) -> list[MoveInfo]:
    """Mainline of a stored game, ply from 1, with %clk seconds when present."""
    parsed = _read_stored(game)
    if parsed is None:
        return []
    return _walk(parsed)[1]


def boards_of(game: Game) -> list[chess.Board]:
    """Boards after each ply; index 0 is the initial position, index n the position after ply n."""
    parsed = _read_stored(game)
    if parsed is None:
        return [chess.Board()]
    board = parsed.board()
    boards = [board.copy()]
    for node in parsed.mainline():
        board.push(node.move)
        boards.append(board.copy())
    return boards


def initial_fen_of(game: Game) -> str:
    parsed = _read_stored(game)
    return parsed.board().fen() if parsed is not None else STANDARD_START


# ---------- schema views ----------


def analysis_status_of(game: Game) -> str:
    """Status of the loaded analysis row; 'none' when it is absent or was not loaded."""
    if "analysis" in inspect(game).unloaded:
        return "none"
    analysis = game.analysis
    return analysis.status if analysis is not None else "none"


def to_summary(game: Game) -> GameSummary:
    return GameSummary(
        id=game.id,
        source=game.source,
        source_id=game.source_id,
        white=game.white,
        black=game.black,
        white_elo=game.white_elo,
        black_elo=game.black_elo,
        result=game.result,
        time_control=game.time_control,
        played_at=game.played_at,
        eco=game.eco,
        opening_name=game.opening_name,
        user_color=game.user_color,  # type: ignore[arg-type]
        ply_count=game.ply_count,
        analysis_status=analysis_status_of(game),  # type: ignore[arg-type]
    )


def to_detail(game: Game) -> GameDetail:
    parsed = _read_stored(game)
    moves: list[MoveInfo]
    if parsed is None:
        initial, moves = STANDARD_START, []
    else:
        start, moves, _ = _walk(parsed)
        initial = start.fen()
    return GameDetail(
        **to_summary(game).model_dump(),
        pgn=game.pgn,
        initial_fen=initial,
        moves=moves,
    )


# ---------- storage ----------


def platform_for(source: str) -> str:
    return source if source in _PLATFORMS else "local"


async def get_or_create_user(session: AsyncSession, username: str, platform: str) -> User:
    """The account for this name and platform, created when it is new.

    Two imports of the same account can run at once (a double-clicked button, a retry), and
    both would pass the SELECT before either INSERT lands. The insert therefore runs in a
    savepoint: on the uq_user_platform conflict the savepoint alone rolls back, the row the
    other request committed is read instead, and the outer transaction survives."""
    username = username.strip()
    stmt = select(User).where(
        func.lower(User.username) == username.lower(), User.platform == platform
    )
    user = await session.scalar(stmt)
    if user is not None:
        return user
    try:
        async with session.begin_nested():
            user = User(username=username, platform=platform)
            session.add(user)
            await session.flush()
    except IntegrityError:
        user = await session.scalar(stmt)
        if user is None:
            raise
    assert user is not None
    return user


async def _existing_by_source_id(
    session: AsyncSession, source: str, ids: list[str]
) -> dict[str, Game]:
    found: dict[str, Game] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        rows = await session.scalars(
            select(Game).where(Game.source == source, Game.source_id.in_(chunk))
        )
        for row in rows:
            assert row.source_id is not None
            found[row.source_id] = row
    return found


async def upsert_games(
    session: AsyncSession,
    parsed: list[ParsedGame],
    source: str,
    username: str | None = None,
) -> ImportResult:
    """Store games that are new for (source, source_id); the rest count as skipped.

    game_ids lists the newly stored rows in input order. When username is given, the games are
    attached to that user (created on first sight, platform = source or 'local' for pgn) and
    user_color is set where the name plays White or Black.

    Each row is inserted in its own savepoint: when a concurrent import of the same games wins
    the race to uq_game_source, that one row counts as skipped instead of the whole request
    failing with an IntegrityError."""
    user: User | None = None
    if username and username.strip():
        user = await get_or_create_user(session, username, platform_for(source))

    ids = [g.source_id for g in parsed if g.source_id is not None]
    existing = await _existing_by_source_id(session, source, ids)

    new_rows: list[Game] = []
    skipped = 0
    seen: set[str] = set()
    for pg in parsed:
        sid = pg.source_id
        if sid is not None:
            if sid in seen:
                skipped += 1
                continue
            seen.add(sid)
            row = existing.get(sid)
            if row is not None:
                skipped += 1
                if user is not None and row.user_id is None:
                    row.user_id = user.id
                    row.user_color = pg.color_of(username)
                continue
        game = Game(
            user_id=user.id if user is not None else None,
            source=source,
            source_id=sid,
            pgn=pg.pgn,
            white=pg.white,
            black=pg.black,
            white_elo=pg.white_elo,
            black_elo=pg.black_elo,
            result=pg.result,
            time_control=pg.time_control,
            played_at=pg.played_at,
            eco=pg.eco,
            opening_name=pg.opening_name,
            user_color=pg.color_of(username),
            ply_count=pg.ply_count,
            headers=dict(pg.headers),
        )
        try:
            async with session.begin_nested():
                session.add(game)
                await session.flush()
        except IntegrityError:
            skipped += 1
            continue
        new_rows.append(game)

    await session.commit()
    return ImportResult(
        imported=len(new_rows),
        skipped=skipped,
        game_ids=[g.id for g in new_rows],
        user_id=user.id if user is not None else None,
    )


async def set_user_ratings(
    session: AsyncSession, user_id: int, rapid: int | None, blitz: int | None
) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(rating_rapid=rapid, rating_blitz=blitz)
    )
    await session.commit()


def _base_query() -> Select[tuple[Game]]:
    return select(Game).options(selectinload(Game.analysis))


async def list_games(
    session: AsyncSession,
    username: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Game]:
    """Newest first (played_at, then id). username filters to that user's games."""
    stmt = _base_query()
    if username and username.strip():
        stmt = stmt.join(User, Game.user_id == User.id).where(
            func.lower(User.username) == username.strip().lower()
        )
    stmt = (
        stmt.order_by(Game.played_at.desc().nulls_last(), Game.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await session.scalars(stmt))


async def get_game(session: AsyncSession, game_id: int) -> Game | None:
    game: Game | None = await session.scalar(_base_query().where(Game.id == game_id))
    return game


async def delete_game(session: AsyncSession, game_id: int) -> bool:
    """Remove the game with its analysis and reviews. Puzzles made from it stay, unlinked."""
    game = await session.get(Game, game_id)
    if game is None:
        return False
    await session.execute(delete(MoveReview).where(MoveReview.game_id == game_id))
    await session.execute(delete(Analysis).where(Analysis.game_id == game_id))
    await session.execute(
        update(Puzzle).where(Puzzle.game_id == game_id).values(game_id=None, ply=None)
    )
    await session.delete(game)
    await session.commit()
    return True


def utc_from_epoch(seconds: int | float | None) -> datetime | None:
    """Naive UTC datetime from a unix timestamp (chess.com end_time)."""
    if seconds is None:
        return None
    return datetime.fromtimestamp(float(seconds), tz=UTC).replace(tzinfo=None)
