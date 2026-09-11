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

M8d-2 adds a streamed path (`stream`) over exactly the same checks. The facts block is built in
four stages (book, engine, plans, maia) that are reported as they finish, the model is asked for
NDJSON — one JSON object per line, one line per section, in a fixed order — and every complete
line is verified and sent on the moment it arrives, so the panel fills in from the top instead
of waiting ten seconds for one JSON answer. Only a finished run is stored: a client that goes
away cancels this generator before it ever reaches the store, and the process is killed with it.
M8d-3 adds the student's own questions (`Addendum`), which are appended to the stored note.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shlex
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
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
    Addendum,
    AddendumRequest,
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

STAGES = ("book", "engine", "plans", "maia")
"""Steps of the facts block, in the order the streamed checklist shows them (plan §10.2)."""
SECTIONS = ("summary", "why", "replies", "alternatives", "traps")
"""Sections the model writes, one NDJSON line each, in this order."""
SERVER_SECTIONS = ("mine", "engine")
"""Sections the server writes itself; they go out right after the stages."""
FIRST_SECTION_SECONDS = 20.0
"""How long a streamed run may stay *silent* before it is killed and the one-shot path is tried
once instead (plan §10.2). Measured from the last thing the process printed, not from the
start: a model that spends a minute on `analyse` and `compare` before writing its first line is
doing what the prompt asked, and killing it there was measured to throw away good runs. The
prose case §10.2 worries about is caught anyway — a run that ends without one readable section
falls back too. A module constant so a test can shorten it."""
STREAM_LIMIT = 1 << 20
"""Longest stream-json line read from the CLI; a tool result can be far past asyncio's 64 KiB."""
PENDING_LIMIT = 1 << 15
"""Longest half-written section object carried over to the next line before it is given up on."""
SUGGESTED_QUESTIONS = 4
"""Most question chips the panel offers (plan §10.3)."""
MAX_ADDENDA = 20
"""Answers kept on one note; the oldest goes when the student adds past this."""
SENTENCE_MARKS = ("—", "?", "!", ".", "…", ":")
"""Punctuation that makes a trap title a sentence rather than a name; a title like
"공짜 폰은 없다 — 5.Nxe5?" cannot be poured into "왜 …인가요?" and has to be quoted instead."""

NO_CLAUDE = "Claude Code를 찾을 수 없습니다. CHAT_CLAUDE_COMMAND를 확인해 주세요."
NO_ANSWER = "Claude Code가 해설을 쓰지 못했습니다. 잠시 뒤 다시 시도해 주세요."
BUSY = "튜터가 다른 답을 쓰는 중입니다. 잠시 뒤 다시 시도해 주세요."
BAD_FEN = "FEN을 읽을 수 없습니다"
ILLEGAL_POSITION = "체스 규칙에 맞지 않는 국면입니다."
ILLEGAL_MOVE = "이 국면에서 둘 수 없는 수입니다"
NO_GAMES = "내 기보에 없는 수입니다."
NO_SECTIONS = "모델이 정해진 형식으로 쓰지 않아 한 번에 쓰는 방식으로 다시 시도합니다."
INCOMPLETE = "해설이 끝나지 않아 저장하지 않았습니다. 다시 만들어 주세요."
NO_NOTE = "이 수의 해설이 아직 없습니다."

Event = dict[str, Any]
"""One line of the note stream, the same shape the chat sends (services.chat)."""


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


@dataclass(frozen=True)
class FactStage:
    """One step of the facts block: the lines it contributes and what the checklist says.

    `detail` is written for the screen (the panel's stage list), `lines` for the model."""

    name: str
    detail: str
    lines: list[str]


def _book_stage(board: chess.Board, move: chess.Move) -> FactStage:
    san = board.san(move)
    after = board.copy(stack=False)
    after.push(move)
    here = lookup(board)
    arrived = lookup(after)
    in_book = _in_book(board, move, after)
    candidates = opening_guide.candidates(board)[:CANDIDATES]
    lines = [
        f"국면 FEN(수를 두기 전): {board.fen()}",
        f"둔 수: {move_label(board.ply() + 1, san)} ({move.uci()})",
        f"수를 둔 뒤 FEN: {after.fen()}",
        f"이 국면의 책 이름: {here.name + ' ' + here.eco if here else '없음(책 밖)'}",
        f"수를 둔 뒤 책 이름: {arrived.name + ' ' + arrived.eco if arrived else '없음(책 밖)'}",
        f"책에 있는 수인가: {'예' if in_book else '아니오'}",
    ]
    if candidates:
        listed = ", ".join(
            f"{c.san}({c.name or '이름 없음'}{' ' + c.eco if c.eco else ''})" for c in candidates
        )
        lines.append(f"이 국면의 책 후보: {listed}")
    detail = (
        f"{len(candidates)}가지 · " + ", ".join(c.san for c in candidates)
        if candidates
        else "책 후보 없음"
    )
    return FactStage("book", f"{detail} · {'책 수' if in_book else '책 밖'}", lines)


def _engine_stage(board: chess.Board) -> FactStage:
    started = time.monotonic()
    lines = _engine_lines(board)
    elapsed = time.monotonic() - started
    if not lines:
        return FactStage("engine", "엔진 없음", [])
    detail = f"{len(lines)}라인 · 깊이 {NOTE_DEPTH} · {elapsed:.1f}초"
    return FactStage("engine", detail, [f"엔진 라인(깊이 {NOTE_DEPTH}): " + " | ".join(lines)])


def _plans_stage(board: chess.Board, move: chess.Move) -> FactStage:
    after = board.copy(stack=False)
    after.push(move)
    info = classify(after)
    side: kb.Side = "white" if board.turn == chess.WHITE else "black"
    plans = kb.plan_specs(info.key, side, kb.mirrored(info.key, after))
    lines = [f"수를 둔 뒤 폰 구조: {info.name}({info.key})"]
    if plans:
        lines.append("그 구조의 계획: " + " / ".join(f"{p.title} — {p.condition}" for p in plans))
    detail = f"{info.name} · 계획 {len(plans)}개" if plans else f"{info.name} · 계획 없음"
    return FactStage("plans", detail, lines)


def _maia_stage(board: chess.Board, rating: int) -> FactStage:
    moves = _maia_moves(board, rating)
    if not moves:
        return FactStage("maia", "마이아 없음", [])
    return FactStage(
        "maia",
        f"마이아 {rating} · " + " · ".join(moves),
        [f"이 레이팅대({rating})가 두는 수: {', '.join(moves)}"],
    )


def fact_stage(name: str, board: chess.Board, move: chess.Move, rating: int) -> FactStage:
    """One stage by name. Blocking (engine, Maia); the streamed path runs it in a thread."""
    if name == "book":
        return _book_stage(board, move)
    if name == "engine":
        return _engine_stage(board)
    if name == "plans":
        return _plans_stage(board, move)
    return _maia_stage(board, rating)


def facts_block(board: chess.Board, move: chess.Move, rating: int) -> str:
    """Everything the model may state, as text. It gets no other source of chess truth."""
    stages = [fact_stage(name, board, move, rating) for name in STAGES]
    return "\n".join(line for stage in stages for line in stage.lines)


PROMPT_HEAD = """\
너는 체스 오프닝 해설을 쓰는 튜터다. 아래 국면에서 둔 한 수에 대한 깊은 해설을 쓴다.

{facts}

"""

PROMPT_RULES = """\
규칙:
- 위 사실 블록과 MCP 도구(analyse, compare, motifs, maia_probs, features)로 확인한 것만 쓴다.
  기억에 의존한 수순·이름·숫자는 쓰지 않는다.
- 칸 이름이나 기물 위치를 말하는 문장은 [[문장]] 으로 감싸고, claims 배열에 그 문장의 근거를
  넣는다. 근거가 없으면 감싸지 않는다. 서버가 검증에 실패한 문장은 표기를 벗긴다.
- claims 원소: {"text": 감싼 문장 그대로, "kind": "attacks|defends|is_check|checkmate|
  piece_on|square_empty|legal_move", "fen": 그 사실이 성립하는 국면 FEN, "subject": 칸 이름,
  "object": 칸 이름 또는 기물 기호(piece_on: P N B R Q K, 소문자는 흑) 또는 SAN(legal_move)}
  fen을 생략하면 수를 둔 뒤 국면으로 본다.
- traps[].line_san 은 **수를 둔 뒤 국면**부터의 SAN 목록이다. 서버가 재생해 보고 불법이면 버린다.
- 한국어로 쓴다. summary는 2~3문장, why는 문단 1~3개, replies·alternatives는 [SAN, 설명] 쌍.
- mine(내 기보)과 engine(엔진 판정)은 서버가 채우므로 쓰지 않는다.

"""

PROMPT_ONE_JSON = """\
아래 JSON 하나만 출력한다(코드펜스·설명 없이):
{"summary": "...", "why": ["..."], "replies": [["SAN", "..."]],
  "alternatives": [["SAN", "..."]],
  "traps": [{"title": "...", "line_san": ["SAN"], "text": "..."}],
  "claims": [{"text": "...", "kind": "attacks", "fen": "...", "subject": "b5",
  "object": "c6"}]}
"""

PROMPT_NDJSON = """\
아래 다섯 줄을 이 순서대로 출력한다. 한 줄에 완성된 JSON 객체 하나씩이고, 줄 안에 줄바꿈을
넣지 않는다. 코드펜스·설명·빈 줄 없이 줄만 낸다. 인사말이나 "이제 씁니다" 같은 말을 앞에
붙이지 않는다 — 첫 글자가 { 여야 한다. 한 줄을 다 쓰면 바로 다음 줄로 넘어간다
(사용자 화면에는 줄이 도착하는 대로 해설이 채워진다).
{"section": "summary", "text": "...", "claims": [...]}
{"section": "why", "paragraphs": ["...", "..."], "claims": [...]}
{"section": "replies", "items": [["SAN", "..."], ["SAN", "..."]], "claims": [...]}
{"section": "alternatives", "items": [["SAN", "..."]], "claims": [...]}
{"section": "traps", "items": [{"title": "...", "line_san": ["SAN"], "text": "..."}], "claims": [...]}
claims에는 그 줄에서 [[…]]로 감싼 문장의 근거만 넣는다. 쓸 내용이 없는 섹션도 빈 값으로
(items: [], paragraphs: []) 한 줄을 낸다.
"""  # noqa: E501


def build_prompt(board: chess.Board, move: chess.Move, rating: int) -> str:
    """The one-shot prompt: the facts, the rules, and one JSON object as the answer."""
    return (
        PROMPT_HEAD.format(facts=facts_block(board, move, rating))
        + PROMPT_RULES
        + (PROMPT_ONE_JSON)
    )


def build_stream_prompt(facts: str) -> str:
    """The streamed prompt: the same facts and rules, one JSON line per section (plan §10.2)."""
    return PROMPT_HEAD.format(facts=facts) + PROMPT_RULES + PROMPT_NDJSON


# ---------- the Claude Code process ----------


def mcp_config() -> dict[str, Any]:
    """The chess tool server, without a chat session: a note draws no boards."""
    return {
        "mcpServers": {
            chat_svc.SERVER_NAME: {"type": "http", "url": chat_svc.mcp_url()},
        }
    }


def build_command(stream: bool = False) -> list[str]:
    """`claude -p` for one note: one JSON answer, this server's chess tools and nothing else.

    With `stream` the CLI reports every event as it happens instead of one envelope at the end
    (`--output-format stream-json --include-partial-messages`, which the CLI only accepts
    together with `--verbose`), so the text deltas can be cut into section lines while they
    are being written. Never `--bare`: that mode does not read the subscription login
    (services.chat)."""
    settings = get_settings()
    fmt = ["stream-json", "--verbose", "--include-partial-messages"] if stream else ["json"]
    return [
        *shlex.split(settings.chat_claude_command),
        "-p",
        "--output-format",
        *fmt,
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


# ---------- the streamed Claude Code process (M8d-2) ----------


@dataclass
class _LineParser:
    """Claude Code's stream-json turned into the three things a note cares about: the tool
    calls it makes, the finished text lines it writes, and how the run ended.

    Text arrives as deltas that do not respect line boundaries, so they are accumulated and cut
    at every newline; a partial last line is only released when the run ends (`flush`)."""

    buffer: str = ""
    saw_delta: bool = False
    tools: set[str] = field(default_factory=set)

    def _text(self, text: str) -> list[Event]:
        self.buffer += text
        out: list[Event] = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                out.append({"type": "_line", "line": line})
        return out

    def flush(self) -> list[Event]:
        line, self.buffer = self.buffer, ""
        return [{"type": "_line", "line": line}] if line.strip() else []

    def feed(self, raw: str) -> list[Event]:
        line = raw.strip()
        if not line:
            return []
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            log.debug("opening note: non-json line from claude: %s", line[:200])
            return []
        if not isinstance(item, dict):
            return []
        kind = item.get("type")
        if kind == "stream_event":
            event = item.get("event") or {}
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self.saw_delta = True
                return self._text(str(delta.get("text", "")))
            return []
        if kind == "assistant":
            return self._assistant(item.get("message") or {})
        if kind == "system":
            return self._system(item)
        if kind == "result":
            return self._result(item)
        return []

    def _assistant(self, message: dict[str, Any]) -> list[Event]:
        out: list[Event] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id", ""))
                if tool_id in self.tools:
                    continue
                self.tools.add(tool_id)
                out.append(
                    {
                        "type": "tool",
                        "name": chat_svc.short_tool_name(str(block.get("name", ""))),
                        "input": block.get("input"),
                    }
                )
            elif block.get("type") == "text" and not self.saw_delta:
                # No partial messages for this block (an older CLI, or a replayed fake): the
                # whole block arrives at once.
                out += self._text(str(block.get("text", "")))
        return out

    def _system(self, item: dict[str, Any]) -> list[Event]:
        if item.get("subtype") != "init":
            return []
        for server in item.get("mcp_servers") or []:
            if isinstance(server, dict) and server.get("name") == chat_svc.SERVER_NAME:
                if server.get("status") != "connected":
                    return [
                        {
                            "type": "warning",
                            "message": f"체스 도구 서버 연결 실패({server.get('status')}). "
                            "해설에 엔진 근거가 빠질 수 있습니다.",
                        }
                    ]
                return []
        return [{"type": "warning", "message": "체스 도구 서버가 로드되지 않았습니다."}]

    def _result(self, item: dict[str, Any]) -> list[Event]:
        out = self.flush()
        subtype = str(item.get("subtype", "")) or "success"
        ok = subtype == "error_max_turns" or not item.get("is_error")
        if not ok:
            detail = item.get("result") or item.get("error") or subtype
            out.append({"type": "error", "message": f"Claude Code 오류: {detail}"})
        out.append({"type": "_done", "ok": ok})
        return out


async def _claude_stream(prompt: str) -> AsyncGenerator[Event]:
    """One streamed `claude -p` run: `tool`, `warning`, `error` events and the raw text lines
    (`_line`) it wrote, in order. The process is killed however the consumer stops — a client
    that closes the connection cancels this generator, and the CLI goes with it."""
    if not chat_svc.availability()["available"]:
        raise NoteUnavailable(NO_CLAUDE)
    try:
        proc = await asyncio.create_subprocess_exec(
            *build_command(stream=True),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(chat_svc.workdir()),
            env=chat_svc.subprocess_env(),
            start_new_session=True,
            limit=STREAM_LIMIT,
        )
    except OSError as exc:
        raise NoteUnavailable(f"{NO_CLAUDE} ({exc})") from exc
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    parser = _LineParser()
    stderr_task = asyncio.create_task(proc.stderr.read())
    saw_done = False
    try:
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the process died before reading it; its stderr says why below
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:  # a line past STREAM_LIMIT: the rest of the run is unreadable
                log.warning("opening note: a stream-json line was too long to read")
                break
            if not raw:
                break
            produced = parser.feed(raw.decode(errors="replace"))
            if not produced:
                # A delta that did not finish a line, or a line this parser ignores. It is
                # still a sign of life, which is what the first-section deadline measures.
                yield {"type": "_progress"}
            for event in produced:
                if event.get("type") == "_done":
                    saw_done = True
                yield event
        if not saw_done:
            for event in parser.flush():
                yield event
            text = (await stderr_task).decode(errors="replace").strip().splitlines()
            detail = text[-1] if text else f"exit code {await proc.wait()}"
            yield {"type": "error", "message": f"{NO_ANSWER} ({detail})"}
            yield {"type": "_done", "ok": False}
    finally:
        chat_svc._terminate(proc)
        if not stderr_task.done():
            stderr_task.cancel()
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), chat_svc.REAP_SECONDS)


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
    note.questions = suggested_questions(note)
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
    if row is None:
        return None
    note = OpeningNote.model_validate(row.payload)
    # Made fresh on every read, so a note stored before a rule changed still offers the
    # questions this version would ask.
    note.questions = suggested_questions(note)
    return note


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

    engine = await _engine_line(board, move, rating)
    mine = await _mine(session, req.username, board, move)
    return await _finish(session, board, move, raw, engine=engine, mine=mine)


def _llm_sources() -> list[str]:
    """What a generated note was built from; `assemble` adds book and plans when they apply."""
    return ["llm", "maia", *(["engine"] if find_stockfish() is not None else [])]


async def _engine_line(board: chess.Board, move: chess.Move, rating: int) -> str | None:
    """The off-book verdict, or None for a move the book knows (plan §9.2)."""
    after = board.copy(stack=False)
    after.push(move)
    if _in_book(board, move, after):
        return None
    return await asyncio.to_thread(_engine_verdict, board, board.san(move), rating)


async def _finish(
    session: AsyncSession,
    board: chess.Board,
    move: chess.Move,
    raw: dict[str, Any],
    *,
    engine: str | None,
    mine: str | None,
) -> OpeningNote:
    """Assemble what the model wrote (streamed or in one piece) and store it."""
    note = await asyncio.to_thread(
        assemble,
        board,
        move,
        raw,
        model=get_settings().chat_model,
        sources=_llm_sources(),
        engine=engine,
        mine=mine,
    )
    return await _store(session, note)


# ---------- streaming one note, section by section (M8d-2) ----------


def _section_raw(name: str, data: dict[str, Any]) -> Any:
    """One NDJSON line's content in the shape `assemble` reads for that section."""
    if name == "summary":
        return str(data.get("text", ""))
    if name == "why":
        return [str(text) for text in data.get("paragraphs") or []]
    if name in ("replies", "alternatives"):
        return data.get("items")
    return data.get("items")  # traps: left raw, _traps replays every line


def verify_section(name: str, raw: Any, claims: object, after: chess.Board) -> Event:
    """One section, checked the moment it arrives: the same demotion and the same trap replay
    the stored note goes through, counted for this section alone.

    The payload is JSON as the panel reads it — a string, a list of paragraphs, [SAN, 설명]
    pairs, or trap objects."""
    verifier = _Verifier(_claims_by_sentence(claims), after.fen())
    payload: Any
    if name == "summary":
        payload = verifier.apply(str(raw or ""))
    elif name == "why":
        payload = [verifier.apply(str(text)) for text in raw or []]
    elif name in ("replies", "alternatives"):
        payload = [list(pair) for pair in verifier.pairs(_pairs(raw))]
    elif name == "traps":
        payload = [trap.model_dump(mode="json") for trap in _traps(raw, after, verifier)]
    else:  # mine, engine: the server's own text, nothing to verify
        payload = raw
    return {
        "type": "section",
        "name": name,
        "payload": payload,
        "verified_claims": verifier.verified,
        "total_claims": verifier.total,
    }


def payload_sections(raw: dict[str, Any], after: chess.Board) -> list[Event]:
    """A whole one-shot answer as section events, for the fallback path. Claims are global
    there, so every section is checked against the same list."""
    claims = raw.get("claims")
    return [
        verify_section(name, raw.get(name), claims, after)
        for name in SECTIONS
        if raw.get(name) not in (None, "", [])
    ]


async def stream(session: AsyncSession, req: NoteRequest) -> AsyncIterator[Event]:
    """Write the note for one move and report it as it is being written (plan §10.2).

    Stages while the facts block is built, then the two sections the server fills itself, then
    one `section` event per NDJSON line the model writes — each verified on arrival — and
    finally the stored note. Nothing is stored unless the run finishes: a client that goes away
    cancels this generator, which kills the CLI on the way out.

    Raises ValueError for a bad FEN or move before anything is yielded, so the endpoint can
    answer 422 instead of opening a stream."""
    board = _board(req.fen)
    move = _move(board, req.san)
    after = board.copy(stack=False)
    after.push(move)
    stored = await load(session, req.fen, req.san)
    if stored is not None and not req.regenerate:
        yield {"type": "note", "note": stored.model_dump(mode="json")}
        return

    rating = get_settings().default_rating
    lines: list[str] = []
    for name in STAGES:
        stage = await asyncio.to_thread(fact_stage, name, board, move, rating)
        lines += stage.lines
        yield {"type": "stage", "name": stage.name, "detail": stage.detail}

    # The server's own sections do not depend on the model, so they go out first (plan §10.2).
    engine = await _engine_line(board, move, rating)
    mine = await _mine(session, req.username, board, move)
    for name, text in (("mine", mine), ("engine", engine)):
        if text:
            yield verify_section(name, text, None, after)

    slots = chat_svc._slots()
    try:
        await asyncio.wait_for(slots.acquire(), chat_svc.QUEUE_WAIT_SECONDS)
    except TimeoutError:
        yield {"type": "error", "message": BUSY}
        return
    try:
        raw: dict[str, Any] = {"claims": []}
        fell_back = False
        failed = False
        sections = _sections(build_stream_prompt("\n".join(lines)), after, raw)
        try:
            async for event in sections:
                if event.get("type") == "_fallback":
                    fell_back = True
                    break
                failed = failed or event.get("type") == "error"
                yield event
        finally:
            await sections.aclose()
        if fell_back:
            payload = await _ask_claude(await asyncio.to_thread(build_prompt, board, move, rating))
            raw = payload
            for event in payload_sections(payload, after):
                yield event
        elif failed or not raw.get("summary"):
            # A run that broke off wrote a real part of a note, but not a whole one: the panel
            # keeps what arrived and marks it 미완성, and the table stays as it was.
            yield {"type": "warning", "message": INCOMPLETE}
            return
    except NoteUnavailable as exc:
        yield {"type": "error", "message": str(exc)}
        return
    finally:
        slots.release()
    note = await _finish(session, board, move, raw, engine=engine, mine=mine)
    yield {"type": "note", "note": note.model_dump(mode="json")}


async def _sections(prompt: str, after: chess.Board, raw: dict[str, Any]) -> AsyncGenerator[Event]:
    """The model's NDJSON turned into `section` events, `raw` filled in as they arrive.

    Yields `_fallback` (and stops) when the run produced no readable section — because nothing
    arrived within FIRST_SECTION_SECONDS and the process was killed, or because it ran to the
    end writing prose. An error the run reports after at least one section is passed on as an
    event instead: the sections that did arrive are worth showing, they are simply not stored."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FIRST_SECTION_SECONDS
    seen: set[str] = set()
    pending = ""
    """The start of a section object the model split across lines (see `_read_line`)."""
    events = _claude_stream(prompt)
    try:
        while True:
            if seen:
                event = await anext(events, None)
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    event = await asyncio.wait_for(anext(events), remaining)
                except StopAsyncIteration:
                    event = None
            if event is None:
                break
            # Anything the run produced — a tool call, a delta, a line — restarts the clock:
            # the deadline is there for a process that says nothing at all, and a model that
            # spends a minute on tools before writing is doing exactly what it was asked to.
            deadline = loop.time() + FIRST_SECTION_SECONDS
            kind = event.get("type")
            if kind == "_done":
                break
            if kind == "_progress":
                continue
            if kind != "_line":
                yield event
                continue
            section, pending = _read_line(str(event.get("line", "")), seen, pending)
            if section is None:
                continue
            name = str(section["section"])
            seen.add(name)
            raw[name] = _section_raw(name, section)
            claims = section.get("claims")
            if isinstance(claims, list):
                raw["claims"] = [*raw.get("claims", []), *claims]
            yield verify_section(name, raw[name], claims, after)
        if not seen:  # the run ended without ever writing a section (prose, or nothing at all)
            yield {"type": "warning", "message": NO_SECTIONS}
            yield {"type": "_fallback"}
    except TimeoutError:
        log.info("opening note: no section in %.0fs; falling back", FIRST_SECTION_SECONDS)
        yield {"type": "warning", "message": NO_SECTIONS}
        yield {"type": "_fallback"}
    finally:
        await events.aclose()


def _object(text: str) -> dict[str, Any] | None:
    """The JSON object in a line, with any prose the model put after it trimmed off."""
    for candidate in (text, text[: text.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _read_line(line: str, seen: set[str], pending: str) -> tuple[dict[str, Any] | None, str]:
    """One output line as a section object, and what to carry over to the next line.

    None for prose, a code fence or a section that was already written. Two habits of a real
    run are forgiven, because either one would cost a whole section:

    * a sentence in front of the first object ("아래에 씁니다.{"section": "summary"…") — measured
      against the real CLI; everything before the first ``{`` is dropped;
    * an object split over two lines by a raw newline inside a string — a line that opens an
      object without closing it is held and joined to the next one.
    """
    text = FENCE.sub("", line).strip()
    if pending:
        text = f"{pending} {text}".strip()
    else:
        start = text.find("{")
        if start < 0:
            return None, ""
        text = text[start:]
    loaded = _object(text)
    if loaded is None:
        held = text if len(text) <= PENDING_LIMIT else ""
        if not held:
            log.info("opening note: dropped an unreadable line (%d chars)", len(text))
        return None, held
    name = loaded.get("section")
    if not isinstance(name, str) or name not in SECTIONS:
        log.info("opening note: a line was JSON but not a section: %s", text[:120])
        return None, ""
    return (None, "") if name in seen else (loaded, "")


# ---------- the tutor chat about a note (M8d-3) ----------


def _trap_question(title: str) -> str:
    if any(mark in title for mark in SENTENCE_MARKS):
        return f'"{title}" 함정을 자세히 설명해 주세요.'
    return f"왜 {title}인가요?"


def suggested_questions(note: OpeningNote) -> list[str]:
    """Question chips made from the note itself, no model involved (plan §10.3).

    Traps, alternatives and replies each give one question, in that order; a move the book does
    not know asks about that first, because it is the thing the student came for."""
    out: list[str] = []
    if not note.in_book:
        out.append("이 수가 나쁘지 않다면 왜 책에 없나요?")
    out += [_trap_question(trap.title.strip()) for trap in note.traps if trap.title.strip()]
    out += [
        f"{note.san} 대신 {san}는 왜 안 되나요?" for san, _text in note.alternatives if san.strip()
    ]
    out += [f"상대가 {san}로 응수하면 내 계획은?" for san, _text in note.replies if san.strip()]
    return list(dict.fromkeys(out))[:SUGGESTED_QUESTIONS]


async def add_addendum(session: AsyncSession, req: AddendumRequest) -> OpeningNote:
    """Keep one tutor answer on this move's note and return the note as it now stands.

    Raises ValueError for a bad FEN or move (`load` parses both), or when there is no note to
    append to — the chat is opened from a note, so that only happens if it was regenerated
    meanwhile."""
    note = await load(session, req.fen, req.san)
    if note is None:
        raise ValueError(NO_NOTE)
    addendum = Addendum(
        question=req.question.strip(),
        answer=req.answer.strip(),
        boards=req.boards,
        unverified=req.unverified,
        created_at=models.utcnow(),
    )
    note = note.model_copy(update={"addenda": [*note.addenda, addendum][-MAX_ADDENDA:]})
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
