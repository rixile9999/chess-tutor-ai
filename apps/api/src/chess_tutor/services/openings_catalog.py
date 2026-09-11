"""Curated opening catalogue for practice: ~30 tabiyas resolved from the bundled lichess TSV.

The only data written by hand is the name of each entry: an ECO code, the exact `name` column
of a row in ``assets/openings_*.tsv``, and — when several rows share that name because the same
variation is reached by different move orders — the length of the line that picks one of them.
Moves, tabiya FEN, pawn structure and plans are all derived, so a typo fails a test instead of
shipping a wrong line (tests/test_openings_catalog.py resolves every entry).

  CATALOG      the entries, in display order, grouped by family
  cards()      one card per entry: line, tabiya FEN, structure, and the user's record
  detail()     a card plus the plans of both sides and the FEN after each move
  book_moves() the book continuations of any position (transpositions included)
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from functools import lru_cache

import chess
import chess.pgn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import openings, structure
from chess_tutor.models import Game
from chess_tutor.schemas import (
    BookMove,
    BookMoves,
    Color,
    OpeningCard,
    OpeningDetail,
    OpeningRecord,
    Plan,
    StructureInfo,
)
from chess_tutor.services import plans as kb
from chess_tutor.services import users

FAMILY_LABELS: dict[str, str] = {
    "e4e5": "1.e4 e5",
    "sicilian": "시실리안",
    "e4other": "1.e4 기타",
    "d4d5": "1.d4 d5",
    "indian": "1.d4 인디언",
    "flank": "플랭크",
}

BOTH_SIDES: tuple[Color, ...] = ("white", "black")

RECORD_PLIES = 30
"""How far a stored game is replayed when looking for a tabiya. The deepest catalogue line is
well inside this, and stopping early keeps the card list cheap on a big collection."""

PRACTICE_SOURCE = "practice"
OPENING_ID_HEADER = "OpeningId"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    family: str
    name_ko: str
    eco: str
    tsv_name: str
    ply: int | None = None
    """Length of the line, to pick one row when several share (eco, tsv_name)."""
    sides: tuple[Color, ...] = BOTH_SIDES


CATALOG: tuple[CatalogEntry, ...] = (
    # ---------- 1.e4 e5 ----------
    CatalogEntry(
        "italian-giuoco-piano",
        "e4e5",
        "이탈리안 (지오코 피아노)",
        "C50",
        "Italian Game: Giuoco Piano",
        6,
    ),
    CatalogEntry(
        "italian-pianissimo",
        "e4e5",
        "지오코 피아니시모",
        "C50",
        "Italian Game: Giuoco Pianissimo",
        10,
    ),
    CatalogEntry(
        "ruy-lopez-closed", "e4e5", "루이 로페즈 클로즈드", "C84", "Ruy Lopez: Closed", 10
    ),
    CatalogEntry(
        "scotch-classical", "e4e5", "스카치 클래시컬", "C45", "Scotch Game: Classical Variation", 8
    ),
    CatalogEntry(
        "petrov-classical",
        "e4e5",
        "페트로프 클래시컬 어택",
        "C42",
        "Petrov's Defense: Classical Attack",
        9,
    ),
    # ---------- Sicilian ----------
    CatalogEntry(
        "sicilian-najdorf",
        "sicilian",
        "나이도르프",
        "B95",
        "Sicilian Defense: Najdorf Variation",
        12,
    ),
    CatalogEntry(
        "sicilian-dragon", "sicilian", "드래곤", "B72", "Sicilian Defense: Dragon Variation", 11
    ),
    CatalogEntry(
        "sicilian-scheveningen",
        "sicilian",
        "셰베닝겐 클래시컬",
        "B83",
        "Sicilian Defense: Scheveningen Variation, Classical Variation",
        11,
    ),
    CatalogEntry(
        "sicilian-sveshnikov",
        "sicilian",
        "스베시니코프",
        "B33",
        "Sicilian Defense: Lasker-Pelikan Variation, Sveshnikov Variation",
        16,
    ),
    CatalogEntry(
        "sicilian-alapin-iqp",
        "sicilian",
        "알라핀 (고립 d폰)",
        "B22",
        "Sicilian Defense: Alapin Variation, Barmen Defense, Central Exchange",
        12,
    ),
    CatalogEntry(
        "sicilian-maroczy",
        "sicilian",
        "악셀러레이티드 드래곤 마로치 바인드",
        "B36",
        "Sicilian Defense: Accelerated Dragon, Maróczy Bind",
        9,
    ),
    CatalogEntry(
        "sicilian-taimanov-hedgehog",
        "sicilian",
        "타이마노프 헤지호그",
        "B44",
        "Sicilian Defense: Taimanov Variation, Modern Line",
        20,
    ),
    # ---------- 1.e4 others ----------
    CatalogEntry(
        "french-advance",
        "e4other",
        "프렌치 어드밴스",
        "C02",
        "French Defense: Advance Variation, Main Line",
        11,
    ),
    CatalogEntry(
        "french-winawer",
        "e4other",
        "프렌치 위나워",
        "C18",
        "French Defense: Winawer Variation, Classical Variation",
        12,
    ),
    CatalogEntry(
        "caro-advance",
        "e4other",
        "카로칸 어드밴스",
        "B12",
        "Caro-Kann Defense: Advance, Short Variation",
        9,
    ),
    CatalogEntry(
        "caro-classical",
        "e4other",
        "카로칸 클래시컬",
        "B19",
        "Caro-Kann Defense: Classical Variation",
        14,
    ),
    CatalogEntry(
        "scandinavian-classical",
        "e4other",
        "스칸디나비안",
        "B01",
        "Scandinavian Defense: Classical Variation",
        10,
    ),
    CatalogEntry(
        "pirc-classical",
        "e4other",
        "피르츠 클래시컬",
        "B08",
        "Pirc Defense: Classical Variation, Quiet System",
        9,
    ),
    # ---------- 1.d4 d5 ----------
    CatalogEntry(
        "qgd-exchange-carlsbad",
        "d4d5",
        "QGD 익스체인지 (칼스바드)",
        "D35",
        "Queen's Gambit Declined: Exchange Variation",
        14,
    ),
    CatalogEntry(
        "qgd-orthodox",
        "d4d5",
        "QGD 오소독스",
        "D60",
        "Queen's Gambit Declined: Orthodox Defense",
        12,
    ),
    CatalogEntry(
        "slav-chebanenko",
        "d4d5",
        "슬라브 체바넨코",
        "D15",
        "Slav Defense: Chebanenko Variation",
        10,
    ),
    CatalogEntry(
        "semi-slav-meran",
        "d4d5",
        "세미슬라브 메란",
        "D47",
        "Semi-Slav Defense: Meran Variation",
        14,
    ),
    CatalogEntry(
        "qga-furman", "d4d5", "QGA 퍼먼", "D27", "Queen's Gambit Accepted: Furman Variation", 14
    ),
    CatalogEntry(
        "tarrasch-dubov", "d4d5", "타라시 (고립 d폰)", "D33", "Tarrasch Defense: Dubov Tarrasch", 16
    ),
    CatalogEntry(
        "london-system", "d4d5", "런던 시스템", "D02", "Queen's Pawn Game: London System", 11
    ),
    # ---------- 1.d4 Indian ----------
    CatalogEntry(
        "nimzo-classical",
        "indian",
        "님조 인디언 클래시컬",
        "E32",
        "Nimzo-Indian Defense: Classical Variation, Keres Defense",
        12,
    ),
    CatalogEntry(
        "nimzo-noa-carlsbad",
        "indian",
        "님조 인디언 노아 (칼스바드)",
        "E35",
        "Nimzo-Indian Defense: Classical Variation, Noa Variation",
        10,
    ),
    CatalogEntry(
        "queens-indian-petrosian",
        "indian",
        "퀸즈 인디언 페트로시안",
        "E12",
        "Queen's Indian Defense: Kasparov-Petrosian Variation, Classical Variation",
        12,
    ),
    CatalogEntry(
        "kid-petrosian",
        "indian",
        "킹스 인디언 페트로시안",
        "E92",
        "King's Indian Defense: Petrosian Variation",
        13,
    ),
    CatalogEntry(
        "grunfeld-exchange",
        "indian",
        "그륀펠트 익스체인지",
        "D85",
        "Grünfeld Defense: Exchange Variation, Modern Exchange Variation",
        14,
    ),
    CatalogEntry("benoni-modern", "indian", "모던 베노니", "A61", "Benoni Defense", 12),
    CatalogEntry(
        "catalan-open",
        "indian",
        "카탈란 오픈",
        "E05",
        "Catalan Opening: Open Defense, Classical Line",
        10,
    ),
    # ---------- flank ----------
    CatalogEntry(
        "english-hedgehog",
        "flank",
        "잉글리시 대칭 헤지호그",
        "A30",
        "English Opening: Symmetrical, Hedgehog, Flexible Formation",
        20,
    ),
    CatalogEntry(
        "reti-double-fianchetto",
        "flank",
        "레티 더블 피안케토",
        "A05",
        "Zukertort Opening: Double Fianchetto Attack",
        11,
    ),
)

BY_ID: dict[str, CatalogEntry] = {entry.id: entry for entry in CATALOG}


class CatalogError(LookupError):
    """A catalogue entry does not name exactly one TSV row. Only a test should ever see it."""


@dataclass(frozen=True)
class Resolved:
    """One catalogue entry with everything the TSV says about it."""

    entry: CatalogEntry
    row: openings.Opening
    moves: tuple[chess.Move, ...]
    line_san: tuple[str, ...]
    fens: tuple[str, ...]
    """FEN after each move; index 0 is the standard start."""
    board: chess.Board
    """The tabiya."""
    structure: StructureInfo

    @property
    def key(self) -> str:
        """Position key of the tabiya, so transpositions in the user's games still count."""
        return openings.position_key(self.board)


def resolve(entry: CatalogEntry) -> Resolved:
    """Everything derived from the TSV row this entry names.

    Raises CatalogError when (eco, name, ply) does not select exactly one row, or when its
    line does not replay."""
    rows = openings.find_rows(entry.eco, entry.tsv_name, entry.ply)
    if len(rows) != 1:
        raise CatalogError(
            f"{entry.id}: {entry.eco} '{entry.tsv_name}' ply={entry.ply} matches {len(rows)} rows"
        )
    row = rows[0]
    board = chess.Board()
    sans: list[str] = []
    moves: list[chess.Move] = []
    fens = [board.fen()]
    for token in openings.san_tokens(row.pgn):
        try:
            move = board.parse_san(token)
        except ValueError as exc:  # pragma: no cover - the rows are validated on load
            raise CatalogError(f"{entry.id}: {token} is not legal") from exc
        sans.append(board.san(move))
        moves.append(move)
        board.push(move)
        fens.append(board.fen())
    return Resolved(
        entry=entry,
        row=row,
        moves=tuple(moves),
        line_san=tuple(sans),
        fens=tuple(fens),
        board=board,
        structure=structure.classify(board),
    )


@lru_cache
def resolved() -> tuple[Resolved, ...]:
    """Every entry resolved once per process."""
    return tuple(resolve(entry) for entry in CATALOG)


def find(opening_id: str) -> Resolved | None:
    return next((r for r in resolved() if r.entry.id == opening_id), None)


def tabiya_board(opening_id: str) -> chess.Board | None:
    found = find(opening_id)
    return found.board.copy() if found is not None else None


# ---------- the user's record ----------

_POINTS = {"win": 1.0, "draw": 0.5, "loss": 0.0}


def _outcome(result: str, color: str | None) -> str | None:
    if color not in ("white", "black") or result not in ("1-0", "0-1", "1/2-1/2"):
        return None
    if result == "1/2-1/2":
        return "draw"
    return "win" if (result == "1-0") == (color == "white") else "loss"


@dataclass
class _Tally:
    games: int = 0
    points: float = 0.0
    scored: int = 0

    def add(self, result: str, color: str | None) -> None:
        self.games += 1
        outcome = _outcome(result, color)
        if outcome is not None:
            self.points += _POINTS[outcome]
            self.scored += 1

    @property
    def score(self) -> float | None:
        return round(self.points / self.scored, 3) if self.scored else None


async def _user_games(session: AsyncSession, username: str) -> list[Game]:
    """Every game of every account with this name. Reading a name never creates an account."""
    accounts = await users.find_users(session, username)
    if not accounts:
        return []
    ids = [u.id for u in accounts]
    stmt = select(Game).where(Game.user_id.in_(ids)).order_by(Game.id)
    return list((await session.execute(stmt)).scalars())


def _keys_reached(game: Game) -> set[str]:
    """Position keys the game passes through, up to RECORD_PLIES plies."""
    try:
        parsed = chess.pgn.read_game(io.StringIO(game.pgn or ""))
    except (ValueError, IndexError):
        return set()
    if parsed is None:
        return set()
    board = parsed.board()
    keys = {openings.position_key(board)}
    for i, move in enumerate(parsed.mainline_moves()):
        if i >= RECORD_PLIES or not board.is_legal(move):
            break
        board.push(move)
        keys.add(openings.position_key(board))
    return keys


def records(games: list[Game], entries: tuple[Resolved, ...]) -> dict[str, OpeningRecord]:
    """Record per entry id: imported games that reached the tabiya, and practice games tagged
    with the entry id in their headers."""
    tallies: dict[str, _Tally] = {r.entry.id: _Tally() for r in entries}
    practice: dict[str, _Tally] = {r.entry.id: _Tally() for r in entries}
    by_key: dict[str, list[str]] = {}
    for r in entries:
        by_key.setdefault(r.key, []).append(r.entry.id)

    for game in games:
        if game.source == PRACTICE_SOURCE:
            tagged = (game.headers or {}).get(OPENING_ID_HEADER)
            if isinstance(tagged, str) and tagged in practice:
                practice[tagged].add(game.result, game.user_color)
            continue
        keys = _keys_reached(game)
        for key in keys & by_key.keys():
            for opening_id in by_key[key]:
                tallies[opening_id].add(game.result, game.user_color)

    return {
        opening_id: OpeningRecord(
            games=tally.games,
            score=tally.score,
            practice_games=practice[opening_id].games,
            practice_score=practice[opening_id].score,
        )
        for opening_id, tally in tallies.items()
    }


# ---------- schema views ----------


def _card(r: Resolved, record: OpeningRecord | None) -> OpeningCard:
    return OpeningCard(
        id=r.entry.id,
        family=r.entry.family,
        family_label=FAMILY_LABELS[r.entry.family],
        name=r.entry.name_ko,
        name_en=r.row.name,
        eco=r.row.eco,
        line_san=list(r.line_san),
        tabiya_fen=r.board.fen(),
        structure=r.structure,
        sides=list(r.entry.sides),
        record=record,
    )


async def cards(session: AsyncSession, username: str | None) -> list[OpeningCard]:
    """The whole catalogue. With a username, every card carries that user's record; the record
    is None when no username was asked for (an unknown name gives empty records)."""
    entries = resolved()
    if not username or not username.strip():
        return [_card(r, None) for r in entries]
    games = await _user_games(session, username)
    found = await asyncio.to_thread(records, games, entries)
    return [_card(r, found[r.entry.id]) for r in entries]


def plans_for(r: Resolved, side: Color) -> list[Plan]:
    """Typical plans of one side in the tabiya. No engine, so a plan is 'later' or
    'unavailable' — never 'pv_match'."""
    return kb.match_plans(
        r.structure.key,
        side,
        [],
        r.board,
        [],
        mirror=kb.mirrored(r.structure.key, r.board),
    )


async def detail(
    session: AsyncSession, opening_id: str, username: str | None
) -> OpeningDetail | None:
    r = find(opening_id)
    if r is None:
        return None
    record: OpeningRecord | None = None
    if username and username.strip():
        games = await _user_games(session, username)
        record = (await asyncio.to_thread(records, games, (r,)))[opening_id]
    return OpeningDetail(
        **_card(r, record).model_dump(),
        plans_white=plans_for(r, "white"),
        plans_black=plans_for(r, "black"),
        fens=list(r.fens),
    )


def book_moves(fen: str) -> BookMoves:
    """Book continuations from this position, plus the name of the position itself when the
    book has one. Raises ValueError for a FEN that is not a legal position.

    Names are Korean (`openings.name_ko`): the book has no hand-written Korean name the way a
    catalogue entry does, so the translated asset is the only one there is (plan §10.5). The
    moves are sorted by that Korean name — `openings.next_moves` sorts by the English one."""
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"FEN을 읽을 수 없습니다: {exc}") from exc
    if not board.is_valid():
        raise ValueError("체스 규칙에 맞지 않는 국면입니다.")
    here = openings.lookup(board)
    moves = sorted(
        (
            BookMove(
                san=board.san(move), uci=move.uci(), eco=op.eco, name=openings.name_ko(op.name)
            )
            for move, op in openings.next_moves(board)
        ),
        key=lambda entry: (entry.name, entry.uci),
    )
    return BookMoves(
        fen=board.fen(),
        opening=(
            None
            if here is None
            else BookMove(san="", uci="", eco=here.eco, name=openings.name_ko(here.name))
        ),
        moves=moves,
    )
