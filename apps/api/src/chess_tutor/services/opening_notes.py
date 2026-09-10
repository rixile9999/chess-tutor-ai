"""Deep notes for one move, written by Claude Code and checked against the board (plan §9.2).

The deterministic detectors (`services.opening_intent`) carry the journal's one-liner and its
badges; the explanation panel needs prose they cannot write — why this move and not the book's,
what the opponent does about it, which trap punishes the natural mistake. That prose is written
once per (position key, move, language) by Claude Code running headless under the user's own
subscription (`claude -p`, exactly as `services.chat` launches it) with none of its own tools
and only this server's chess tools over MCP, and is then stored.

What the model writes is never trusted as it stands. The prompt hands it a facts block — the
FEN, the move, the book's continuations, the pawn structure and its plans, Maia's top moves and
three engine lines — and asks for the JSON of `schemas.OpeningNote` with every sentence that
names a square wrapped in ``[[…]]`` and backed by claims. Before anything is stored the server:

* verifies every marked sentence with `verify.verify_all` and **demotes** the ones that fail —
  the brackets come off and the text stays, so the panel shows it as an opinion instead of a
  checked fact (a deletion would tear a hole in the prose);
* replays every trap line from the position after the move and drops the ones that do not play;
* writes `mine` itself from the user's own games (the opening map's edges), and `engine` itself
  from `play_coach.check` for a move that is off the book. The model never writes either.

`assets/opening_notes_seed.json` holds the eight hand-written Ruy Lopez notes of the mockup;
they are loaded on startup when they are not in the table yet and go through the same verifier,
so the page has something to show without Claude Code installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
from dataclasses import dataclass
from importlib import resources
from typing import Any

import chess
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chess_tutor import models
from chess_tutor.config import get_settings
from chess_tutor.engine import find_stockfish, pool
from chess_tutor.models import Game, User
from chess_tutor.openings import lookup, next_moves, position_key
from chess_tutor.schemas import (
    Color,
    NoteRequest,
    OpeningNote,
    PlayCheckRequest,
    TrapLine,
)
from chess_tutor.services import analysis, maia, opening_guide, play_coach, reasoning
from chess_tutor.services import chat as chat_svc
from chess_tutor.services import plans as kb
from chess_tutor.services.openings_map import PRACTICE_SOURCE, build_map, move_label
from chess_tutor.structure import classify
from chess_tutor.verify import Claim, play_line, verify_all

log = logging.getLogger(__name__)

LANG = "ko"
SEED_FILE = "opening_notes_seed.json"
SEED_MODEL = "seed"
"""`model` of a hand-written note, so the panel can say where the text came from."""
TOOL_NAMES = ("analyse", "compare", "motifs", "maia_probs", "features")
"""The chess tools a note may use. `show_board` needs a chat session and has no reader here."""
CANDIDATES = 8
"""Book continuations put in the facts block."""
MAIA_MOVES = 3
ENGINE_LINES = 3
NOTE_DEPTH = 12
"""Search depth of the facts block and of the off-book verdict (plan §9.2)."""
MAP_DEPTH = 24
"""Plies of the user's own map read for the `mine` line; a note this deep is rare."""
MIN_REPLY_GAMES = 2
"""Games before a losing reply is worth naming."""
FENCE = re.compile(r"^```(?:json)?|```$", re.M)
MARK = re.compile(r"\[\[(.+?)\]\]", re.S)
"""A sentence the model claims the verifier can confirm."""

NO_CLAUDE = "Claude Code를 찾을 수 없습니다. CHAT_CLAUDE_COMMAND를 확인해 주세요."
NO_ANSWER = "Claude Code가 해설을 쓰지 못했습니다. 잠시 뒤 다시 시도해 주세요."
BUSY = "튜터가 다른 답을 쓰는 중입니다. 잠시 뒤 다시 시도해 주세요."
BAD_FEN = "FEN을 읽을 수 없습니다"
ILLEGAL_POSITION = "체스 규칙에 맞지 않는 국면입니다."
ILLEGAL_MOVE = "이 국면에서 둘 수 없는 수입니다"
NO_GAMES = "내 기보에 없는 수입니다."


class NoteUnavailable(RuntimeError):
    """Claude Code is missing or could not answer. Nothing is stored; the router answers 503."""


# ---------- position helpers ----------


def _board(fen: str) -> chess.Board:
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"{BAD_FEN}: {exc}") from exc
    if not board.is_valid():
        raise ValueError(ILLEGAL_POSITION)
    return board


def _move(board: chess.Board, san: str) -> chess.Move:
    try:
        return board.parse_san(san)
    except ValueError as exc:
        raise ValueError(f"{ILLEGAL_MOVE}: {san}") from exc


def _in_book(board: chess.Board, move: chess.Move, after: chess.Board) -> bool:
    """The book knows the move here, or the position it reaches (a transposition)."""
    return any(move == known for known, _opening in next_moves(board)) or lookup(after) is not None


# ---------- verification and demotion ----------


def _key(text: str) -> str:
    """Sentences are matched to their claims ignoring whitespace, which the model varies."""
    return "".join(text.split())


def _claims_by_sentence(raw: object) -> dict[str, list[Claim]]:
    """The model's claim list, grouped by the sentence each claim belongs to.

    An entry that is not a claim at all is kept as an empty list, which demotes its sentence:
    an unbacked mark is a failed one, never a silent pass."""
    out: dict[str, list[Claim]] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        entry = out.setdefault(_key(text), [])
        fields = {k: v for k, v in item.items() if k != "text"}
        fields.setdefault("fen", "")  # filled in from the position after the move
        try:
            entry.append(Claim.model_validate(fields))
        except ValueError:
            log.info("opening note: unreadable claim for %r", text[:60])
    return out


@dataclass
class _Verifier:
    """Strips the marks off every sentence whose claims do not all hold, and counts them."""

    claims: dict[str, list[Claim]]
    fen: str
    """Position a claim without a FEN of its own is about: the one after the move."""
    verified: int = 0
    total: int = 0

    def _holds(self, sentence: str) -> bool:
        claims = self.claims.get(_key(sentence), [])
        if not claims:
            self.total += 1
            return False
        checked = [
            claim if claim.fen else claim.model_copy(update={"fen": self.fen}) for claim in claims
        ]
        verdicts = verify_all(checked)
        held = sum(1 for verdict in verdicts if verdict.holds)
        self.total += len(verdicts)
        self.verified += held
        return held == len(verdicts)

    def apply(self, text: str) -> str:
        def one(match: re.Match[str]) -> str:
            inner = match.group(1).strip()
            return f"[[{inner}]]" if self._holds(inner) else inner

        return MARK.sub(one, text)

    def pairs(self, items: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [(san, self.apply(text)) for san, text in items]


def _pairs(raw: object) -> list[tuple[str, str]]:
    """[["a6", "…"], …] as the model wrote it, anything malformed dropped."""
    out: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, list | tuple) and len(item) == 2:
            out.append((str(item[0]), str(item[1])))
        elif isinstance(item, dict) and "san" in item:
            out.append((str(item["san"]), str(item.get("text", ""))))
    return out


def _traps(raw: object, after: chess.Board, verifier: _Verifier) -> list[TrapLine]:
    """The traps whose lines really play from the position after the move; the rest are gone."""
    out: list[TrapLine] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        line = [str(san) for san in item.get("line_san") or []]
        try:
            play_line(after.fen(), line)
        except (ValueError, IndexError):
            log.info("opening note: dropped an illegal trap line %s", line)
            continue
        out.append(
            TrapLine(
                title=str(item.get("title", "")),
                line_san=line,
                text=verifier.apply(str(item.get("text", ""))),
            )
        )
    return out


# ---------- facts for the prompt ----------


def _engine_lines(board: chess.Board) -> list[str]:
    """Three engine lines at NOTE_DEPTH, or nothing when there is no engine to ask."""
    try:
        with pool.borrow() as borrowed:
            lines = analysis.analyse_board(board, NOTE_DEPTH, ENGINE_LINES, borrowed)
    except (RuntimeError, ValueError) as exc:
        log.info("opening note: no engine lines (%s)", exc)
        return []
    return [
        f"{line.rank}. {' '.join(line.pv[:6])} ({reasoning.score_text(line.score)})"
        for line in lines
        if line.pv
    ]


def _maia_moves(board: chess.Board, rating: int) -> list[str]:
    probs, _source = maia.move_probs(board.fen(), rating)
    return [f"{san} {prob * 100:.0f}%" for san, prob in list(probs.items())[:MAIA_MOVES]]


def _engine_verdict(board: chess.Board, san: str, rating: int) -> str | None:
    """The off-book verdict the server writes itself (`play_coach.check`)."""
    try:
        result = play_coach.check(
            PlayCheckRequest(fen_before=board.fen(), san=san, rating=rating, depth=NOTE_DEPTH)
        )
    except (RuntimeError, ValueError) as exc:
        log.info("opening note: no engine verdict (%s)", exc)
        return None
    loss = result.win_loss * 100
    return (
        f"엔진(깊이 {NOTE_DEPTH}): {result.classification} · "
        f"승률 손실 {loss:.1f}% · 최선 {result.best_san}"
    )


def facts_block(board: chess.Board, move: chess.Move, rating: int) -> str:
    """Everything the model may state, as text. It gets no other source of chess truth."""
    san = board.san(move)
    after = board.copy(stack=False)
    after.push(move)
    here = lookup(board)
    arrived = lookup(after)
    info = classify(after)
    side: kb.Side = "white" if board.turn == chess.WHITE else "black"
    plans = kb.plan_specs(info.key, side, kb.mirrored(info.key, after))
    candidates = opening_guide.candidates(board)[:CANDIDATES]
    lines = [
        f"국면 FEN(수를 두기 전): {board.fen()}",
        f"둔 수: {move_label(board.ply() + 1, san)} ({move.uci()})",
        f"수를 둔 뒤 FEN: {after.fen()}",
        f"이 국면의 책 이름: {here.name + ' ' + here.eco if here else '없음(책 밖)'}",
        f"수를 둔 뒤 책 이름: {arrived.name + ' ' + arrived.eco if arrived else '없음(책 밖)'}",
        f"책에 있는 수인가: {'예' if _in_book(board, move, after) else '아니오'}",
    ]
    if candidates:
        listed = ", ".join(
            f"{c.san}({c.name or '이름 없음'}{' ' + c.eco if c.eco else ''})" for c in candidates
        )
        lines.append(f"이 국면의 책 후보: {listed}")
    lines.append(f"수를 둔 뒤 폰 구조: {info.name}({info.key})")
    if plans:
        lines.append("그 구조의 계획: " + " / ".join(f"{p.title} — {p.condition}" for p in plans))
    maia_moves = _maia_moves(board, rating)
    if maia_moves:
        lines.append(f"이 레이팅대({rating})가 두는 수: {', '.join(maia_moves)}")
    engine = _engine_lines(board)
    if engine:
        lines.append(f"엔진 라인(깊이 {NOTE_DEPTH}): " + " | ".join(engine))
    return "\n".join(lines)


PROMPT = """\
너는 체스 오프닝 해설을 쓰는 튜터다. 아래 국면에서 둔 한 수에 대한 깊은 해설을 쓴다.

{facts}

규칙:
- 위 사실 블록과 MCP 도구(analyse, compare, motifs, maia_probs, features)로 확인한 것만 쓴다.
  기억에 의존한 수순·이름·숫자는 쓰지 않는다.
- 칸 이름이나 기물 위치를 말하는 문장은 [[문장]] 으로 감싸고, claims 배열에 그 문장의 근거를
  넣는다. 근거가 없으면 감싸지 않는다. 서버가 검증에 실패한 문장은 표기를 벗긴다.
- claims 원소: {{"text": 감싼 문장 그대로, "kind": "attacks|defends|is_check|checkmate|
  piece_on|square_empty|legal_move", "fen": 그 사실이 성립하는 국면 FEN, "subject": 칸 이름,
  "object": 칸 이름 또는 기물 기호(piece_on: P N B R Q K, 소문자는 흑) 또는 SAN(legal_move)}}
  fen을 생략하면 수를 둔 뒤 국면으로 본다.
- traps[].line_san 은 **수를 둔 뒤 국면**부터의 SAN 목록이다. 서버가 재생해 보고 불법이면 버린다.
- 한국어로 쓴다. summary는 2~3문장, why는 문단 1~3개, replies·alternatives는 [SAN, 설명] 쌍.
- mine(내 기보)과 engine(엔진 판정)은 서버가 채우므로 쓰지 않는다.

아래 JSON 하나만 출력한다(코드펜스·설명 없이):
{{"summary": "...", "why": ["..."], "replies": [["SAN", "..."]],
  "alternatives": [["SAN", "..."]],
  "traps": [{{"title": "...", "line_san": ["SAN"], "text": "..."}}],
  "claims": [{{"text": "...", "kind": "attacks", "fen": "...", "subject": "b5",
  "object": "c6"}}]}}
"""


def build_prompt(board: chess.Board, move: chess.Move, rating: int) -> str:
    return PROMPT.format(facts=facts_block(board, move, rating))


# ---------- the Claude Code process ----------


def mcp_config() -> dict[str, Any]:
    """The chess tool server, without a chat session: a note draws no boards."""
    return {
        "mcpServers": {
            chat_svc.SERVER_NAME: {"type": "http", "url": chat_svc.mcp_url()},
        }
    }


def build_command() -> list[str]:
    """`claude -p` for one note: one JSON answer, this server's chess tools and nothing else.

    Never `--bare`: that mode does not read the subscription login (services.chat)."""
    settings = get_settings()
    return [
        *shlex.split(settings.chat_claude_command),
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp_config()),
        "--allowedTools",
        ",".join(f"mcp__{chat_svc.SERVER_NAME}__{name}" for name in TOOL_NAMES),
        "--max-turns",
        str(settings.chat_max_turns),
        "--model",
        settings.chat_model,
    ]


def _payload(text: str) -> dict[str, Any]:
    """The JSON object in the model's answer, code fences and any prose around it removed."""
    stripped = FENCE.sub("", text).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise NoteUnavailable(NO_ANSWER)
    try:
        loaded = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise NoteUnavailable(f"{NO_ANSWER} ({exc})") from exc
    if not isinstance(loaded, dict):
        raise NoteUnavailable(NO_ANSWER)
    return loaded


async def _ask_claude(prompt: str) -> dict[str, Any]:
    """One `claude -p` run. Raises NoteUnavailable for anything that is not a JSON answer."""
    if not chat_svc.availability()["available"]:
        raise NoteUnavailable(NO_CLAUDE)
    settings = get_settings()
    try:
        proc = await asyncio.create_subprocess_exec(
            *build_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(chat_svc.workdir()),
            env=chat_svc.subprocess_env(),
            start_new_session=True,
        )
    except OSError as exc:
        raise NoteUnavailable(f"{NO_CLAUDE} ({exc})") from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()), settings.chat_timeout_seconds
        )
    except TimeoutError as exc:
        chat_svc._terminate(proc)
        limit = int(settings.chat_timeout_seconds)
        raise NoteUnavailable(f"{limit}초 안에 해설이 끝나지 않았습니다.") from exc
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip().splitlines()
        raise NoteUnavailable(f"{NO_ANSWER} ({detail[-1] if detail else proc.returncode})")
    try:
        envelope = json.loads(stdout.decode(errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise NoteUnavailable(f"{NO_ANSWER} ({exc})") from exc
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        raise NoteUnavailable(NO_ANSWER)
    return _payload(str(envelope.get("result", "")))


# ---------- the user's own record ----------


async def _user_games(session: AsyncSession, username: str, color: Color) -> list[Game]:
    stmt = (
        select(Game)
        .join(User, Game.user_id == User.id)
        .where(
            User.username == username,
            Game.user_color == color,
            Game.source != PRACTICE_SOURCE,
        )
        .order_by(Game.played_at, Game.id)
    )
    return list((await session.execute(stmt)).scalars().all())


def _mine_text(games: list[Game], color: Color, board: chess.Board, move: chess.Move) -> str:
    """What the user's own games say about this move: the edge of the opening map, and the
    reply that beats them most often after it."""
    after = board.copy(stack=False)
    after.push(move)
    source, target = position_key(board), position_key(after)
    opening_map = build_map(games, color, depth=MAP_DEPTH, min_games=1)
    edge = next(
        (e for e in opening_map.edges if e.source == source and e.target == target and e.games),
        None,
    )
    if edge is None:
        return NO_GAMES
    nodes = {node.id: node for node in opening_map.nodes}
    text = f"내 기보 {edge.games}판 · 승률 {edge.score * 100:.0f}%."
    replies = [
        (node, e)
        for e in opening_map.edges
        if e.source == target and (node := nodes.get(e.target)) is not None
        if node.games >= MIN_REPLY_GAMES and node.losses > node.wins
    ]
    if not replies:
        return text
    node, worst = min(replies, key=lambda pair: (pair[0].score, -pair[0].games))
    label = move_label(node.depth, worst.san)
    return f"{text} 그 뒤 {label} 라인에서 {node.wins}승 {node.losses}패."


async def _mine(
    session: AsyncSession, username: str | None, board: chess.Board, move: chess.Move
) -> str | None:
    """None without a user name: the note is then the same for everyone (plan §9.2)."""
    if not username:
        return None
    color: Color = "white" if board.turn == chess.WHITE else "black"
    games = await _user_games(session, username, color)
    if not games:
        return NO_GAMES
    return await asyncio.to_thread(_mine_text, games, color, board, move)


# ---------- assembling and storing ----------


def _sources(board: chess.Board, move: chess.Move, extra: list[str]) -> list[str]:
    after = board.copy(stack=False)
    after.push(move)
    out = list(extra)
    if next_moves(board) or lookup(after) is not None:
        out.insert(0, "book")
    if kb.plan_specs(classify(after).key, "white" if board.turn == chess.WHITE else "black"):
        out.append("plans")
    return list(dict.fromkeys(out))


def assemble(
    board: chess.Board,
    move: chess.Move,
    raw: dict[str, Any],
    *,
    model: str,
    sources: list[str],
    engine: str | None = None,
    mine: str | None = None,
) -> OpeningNote:
    """The model's (or the seed's) JSON turned into a note: marks checked, traps replayed.

    Pure and sync — the same path serves a generated note and a seeded one."""
    after = board.copy(stack=False)
    after.push(move)
    verifier = _Verifier(_claims_by_sentence(raw.get("claims")), after.fen())
    note = OpeningNote(
        position_key=position_key(board),
        san=board.san(move),
        in_book=_in_book(board, move, after),
        summary=verifier.apply(str(raw.get("summary", ""))),
        why=[verifier.apply(str(text)) for text in raw.get("why") or []],
        replies=verifier.pairs(_pairs(raw.get("replies"))),
        alternatives=verifier.pairs(_pairs(raw.get("alternatives"))),
        traps=_traps(raw.get("traps"), after, verifier),
        mine=mine,
        engine=engine,
        sources=_sources(board, move, sources),
        verified_claims=verifier.verified,
        total_claims=verifier.total,
        model=model,
        created_at=models.utcnow(),
    )
    return note


async def _store(session: AsyncSession, note: OpeningNote) -> OpeningNote:
    """Insert or replace the row for this (position, move, language)."""
    row = (
        await session.execute(
            select(models.OpeningNote).where(
                models.OpeningNote.position_key == note.position_key,
                models.OpeningNote.san == note.san,
                models.OpeningNote.lang == LANG,
            )
        )
    ).scalar_one_or_none()
    payload = note.model_dump(mode="json")
    if row is None:
        row = models.OpeningNote(position_key=note.position_key, san=note.san, lang=LANG)
        session.add(row)
    row.payload = payload
    row.model = note.model
    row.verified_claims = note.verified_claims
    row.total_claims = note.total_claims
    row.created_at = note.created_at
    try:
        await session.commit()
    except IntegrityError:  # another request stored the same note first
        await session.rollback()
    return note


async def load(session: AsyncSession, fen: str, san: str) -> OpeningNote | None:
    """The stored note for this move, or None. Raises ValueError for a bad FEN or move."""
    board = _board(fen)
    move = _move(board, san)
    row = (
        await session.execute(
            select(models.OpeningNote).where(
                models.OpeningNote.position_key == position_key(board),
                models.OpeningNote.san == board.san(move),
                models.OpeningNote.lang == LANG,
            )
        )
    ).scalar_one_or_none()
    return OpeningNote.model_validate(row.payload) if row is not None else None


async def write(session: AsyncSession, req: NoteRequest) -> OpeningNote:
    """The note for this move: the stored one, or a new one from Claude Code.

    Raises ValueError for a bad FEN or move and NoteUnavailable when Claude Code is missing,
    busy or could not answer — nothing is stored in that case."""
    board = _board(req.fen)
    move = _move(board, req.san)
    stored = await load(session, req.fen, req.san)
    if stored is not None and not req.regenerate:
        return stored

    rating = get_settings().default_rating
    prompt = await asyncio.to_thread(build_prompt, board, move, rating)
    slots = chat_svc._slots()
    try:
        await asyncio.wait_for(slots.acquire(), chat_svc.QUEUE_WAIT_SECONDS)
    except TimeoutError as exc:
        raise NoteUnavailable(BUSY) from exc
    try:
        raw = await _ask_claude(prompt)
    finally:
        slots.release()

    after = board.copy(stack=False)
    after.push(move)
    engine = (
        None
        if _in_book(board, move, after)
        else await asyncio.to_thread(_engine_verdict, board, board.san(move), rating)
    )
    note = await asyncio.to_thread(
        assemble,
        board,
        move,
        raw,
        model=get_settings().chat_model,
        sources=["llm", "maia", *(["engine"] if find_stockfish() is not None else [])],
        engine=engine,
        mine=await _mine(session, req.username, board, move),
    )
    return await _store(session, note)


# ---------- the seeded notes ----------


def _seed_entries() -> list[dict[str, Any]]:
    text = resources.files("chess_tutor").joinpath("assets").joinpath(SEED_FILE).read_text("utf-8")
    loaded = json.loads(text)
    entries = loaded.get("notes") if isinstance(loaded, dict) else loaded
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def seed_notes() -> list[OpeningNote]:
    """The hand-written notes, keyed by their move list, turned into notes for this book.

    Every ``[[…]]`` sentence goes through the verifier exactly as a generated one does, so a
    seed that stops matching the board is demoted instead of lying."""
    out: list[OpeningNote] = []
    for entry in _seed_entries():
        sans = str(entry.get("moves", "")).split()
        if not sans:
            continue
        board = chess.Board()
        try:
            for san in sans[:-1]:
                board.push_san(san)
            move = board.parse_san(sans[-1])
        except ValueError:
            log.warning("opening note seed: %s does not play", entry.get("moves"))
            continue
        out.append(assemble(board, move, entry, model=SEED_MODEL, sources=["seed"]))
    return out


async def load_seed(session: AsyncSession) -> int:
    """Store the seeded notes that are not in the table yet. Returns how many were added.

    Idempotent: a note the user (or Claude Code) has already written is never overwritten."""
    notes = await asyncio.to_thread(seed_notes)
    added = 0
    for note in notes:
        exists = (
            await session.execute(
                select(models.OpeningNote.id).where(
                    models.OpeningNote.position_key == note.position_key,
                    models.OpeningNote.san == note.san,
                    models.OpeningNote.lang == LANG,
                )
            )
        ).first()
        if exists is not None:
            continue
        await _store(session, note)
        added += 1
    return added
