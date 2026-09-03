"""Position chat: the student argues with the tutor about one move and Claude Code answers.

Layer 4, conversational. Claude Code runs headless (`claude -p`) under the user's own
subscription login, so no API key is involved. It gets none of its built-in tools, only this
server's chess tools over MCP (services.chat_tools), so every number, line and board state it
shows comes from the engine, the detectors or python-chess. Its stream-json output is turned
into the events the review page renders, and the board states the tools push are merged into
the same stream in the order they happen.

One `ChatSession` is one conversation about one ply. Its id doubles as the Claude Code session
id, so the second and later questions resume the stored conversation (`--resume`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chess_tutor import models
from chess_tutor.config import get_settings
from chess_tutor.db import session_factory

log = logging.getLogger(__name__)

SERVER_NAME = "chess"
"""MCP server name; Claude Code exposes its tools as `mcp__chess__<tool>`."""
TOOL_NAMES = ("analyse", "show_board", "compare", "motifs", "maia_probs", "features")
SQUARE = re.compile(r"[a-h][1-8]")
FEN_BOARD = re.compile(r"(?<![\w/])([pnbrqkPNBRQK1-8]+(?:/[pnbrqkPNBRQK1-8]+){7})(?= [wb] )")
"""The piece-placement field of a FEN followed by the side to move, as it appears in facts and
tool results. The squares it occupies are grounded: a piece really stands there."""
MAX_SESSIONS = 64
"""Conversations kept in memory; the oldest idle one is dropped past this."""
RESULT_PREVIEW = 240
LOCK_WAIT_SECONDS = 0.5
"""How long a new question waits for the previous answer of the same conversation."""
QUEUE_WAIT_SECONDS = 60.0
"""How long a question waits for a free Claude Code slot (chat_concurrency)."""
REAP_SECONDS = 3.0
IN_USE = "already in use"
"""Claude Code's complaint when `--session-id` names a session it has already stored."""

Event = dict[str, Any]


def full_tool_names() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]


def short_tool_name(name: str) -> str:
    prefix = f"mcp__{SERVER_NAME}__"
    return name[len(prefix) :] if name.startswith(prefix) else name


def occupied_squares(placement: str) -> set[str]:
    """Squares with a piece on them in a FEN piece-placement field."""
    out: set[str] = set()
    for rank_index, row in enumerate(placement.split("/")):
        rank = 8 - rank_index
        file = 0
        for ch in row:
            if ch.isdigit():
                file += int(ch)
            else:
                if file < 8 and 1 <= rank <= 8:
                    out.add(f"{'abcdefgh'[file]}{rank}")
                file += 1
    return out


# ---------- sessions ----------


@dataclass
class ChatSession:
    id: str
    game_id: int
    ply: int
    system_prompt: str
    prompt_path: Path
    """The system prompt on disk, passed as `--system-prompt-file` (argv has size limits)."""
    known_squares: set[str] = field(default_factory=set)
    """Squares that some fact, tool result or board state has grounded so far. A square the
    tutor mentions outside this set is flagged as unverified in the UI."""
    queue: asyncio.Queue[Event] = field(default_factory=asyncio.Queue)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    loop: asyncio.AbstractEventLoop | None = None
    """The loop the queue belongs to, so a tool running on a worker thread can still push."""
    resumable: bool = False
    """True once Claude Code has stored a conversation under `id`: from the moment its init
    line arrives, whatever happens to the rest of that turn."""
    turns: int = 0
    boards: int = 0
    attempt: int = 0
    """Counts process launches; every event carries the launch it came from, so anything a
    killed process still emits is dropped instead of leaking into the next answer."""
    last_used: float = field(default_factory=time.monotonic)

    def note_squares(self, text: str) -> None:
        """Ground every square named in `text` and every occupied square of any FEN in it."""
        self.known_squares.update(SQUARE.findall(text))
        for placement in FEN_BOARD.findall(text):
            self.known_squares.update(occupied_squares(placement))

    def unverified(self, text: str) -> list[str]:
        """Squares mentioned in `text` that nothing has grounded, in order of first mention."""
        seen: dict[str, None] = {}
        for square in SQUARE.findall(text):
            if square not in self.known_squares:
                seen.setdefault(square, None)
        return list(seen)


_sessions: dict[str, ChatSession] = {}


def get_session(session_id: str | None) -> ChatSession | None:
    if not session_id:
        return None
    session = _sessions.get(session_id)
    if session is not None:
        session.last_used = time.monotonic()
    return session


def create_session(game_id: int, ply: int, system_prompt: str) -> ChatSession:
    session_id = str(uuid.uuid4())
    prompts = workdir() / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    path = prompts / f"{session_id}.md"
    path.write_text(system_prompt, encoding="utf-8")
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    session = ChatSession(
        id=session_id,
        game_id=game_id,
        ply=ply,
        system_prompt=system_prompt,
        prompt_path=path,
        loop=loop,
    )
    session.note_squares(system_prompt)
    _sessions[session.id] = session
    if len(_sessions) > MAX_SESSIONS:
        idle = [s for s in _sessions.values() if s.id != session.id and not s.lock.locked()]
        if idle:
            oldest = min(idle, key=lambda s: s.last_used)
            del _sessions[oldest.id]
            with contextlib.suppress(OSError):
                oldest.prompt_path.unlink()
    return session


def push_board(session: ChatSession, event: Event) -> None:
    """Called by the show_board tool: put a board state into the session's stream. Safe from a
    worker thread too (the queue is bound to the loop the session was created on)."""
    session.boards += 1
    event.setdefault("type", "board")
    event["n"] = session.boards
    event["attempt"] = session.attempt
    session.note_squares(json.dumps(event, ensure_ascii=False))
    try:
        running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is None and session.loop is not None and session.loop.is_running():
        session.loop.call_soon_threadsafe(session.queue.put_nowait, event)
    else:
        session.queue.put_nowait(event)


def _drain(queue: asyncio.Queue[Event]) -> None:
    while not queue.empty():
        queue.get_nowait()


# ---------- the Claude Code process ----------


def workdir() -> Path:
    settings = get_settings()
    path = (
        Path(settings.chat_workdir).expanduser()
        if settings.chat_workdir
        else Path.home() / ".cache" / "chess-tutor" / "chat"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def mcp_url() -> str:
    """Where the subprocess reaches this server's tools: the setting, else this server's own
    port (scripts/server.sh exports API_PORT)."""
    configured = get_settings().chat_mcp_url
    if configured:
        return configured
    return f"http://127.0.0.1:{os.environ.get('API_PORT', '8000')}/mcp/"


def mcp_config(session: ChatSession) -> dict[str, Any]:
    return {
        "mcpServers": {
            SERVER_NAME: {
                "type": "http",
                "url": mcp_url(),
                "headers": {"X-Chat-Session": session.id},
            }
        }
    }


def build_command(session: ChatSession) -> list[str]:
    """The `claude -p` invocation for one turn. The question itself goes in on stdin.

    No built-in tools (`--tools ""`), only the chess MCP server (`--strict-mcp-config`), and
    those tools pre-approved so a headless run never waits for a permission prompt. Never
    `--bare`: that mode does not read the subscription login."""
    settings = get_settings()
    command = [
        *shlex.split(settings.chat_claude_command),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp_config(session)),
        "--allowedTools",
        ",".join(full_tool_names()),
        "--max-turns",
        str(settings.chat_max_turns),
        "--model",
        settings.chat_model,
        "--system-prompt-file",
        str(session.prompt_path),
    ]
    command += ["--resume", session.id] if session.resumable else ["--session-id", session.id]
    return command


def subprocess_env() -> dict[str, str]:
    """The subscription login must be used: an API key in the environment would take precedence
    and bill the Console account instead."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    env.setdefault("DISABLE_AUTOUPDATER", "1")
    return env


def availability() -> dict[str, Any]:
    settings = get_settings()
    try:
        words = shlex.split(settings.chat_claude_command)
    except ValueError as exc:
        return {
            "available": False,
            "command": settings.chat_claude_command,
            "model": settings.chat_model,
            "reason": f"CHAT_CLAUDE_COMMAND을 읽지 못했습니다: {exc}",
        }
    executable = words[0] if words else ""
    found = shutil.which(executable) if executable else None
    if found is None and executable and Path(executable).exists():
        found = executable
    return {
        "available": found is not None,
        "command": settings.chat_claude_command,
        "model": settings.chat_model,
        "reason": None if found else f"'{executable}' 실행 파일을 찾지 못했습니다.",
    }


def format_message(text: str, move_fen: str | None, move_san: str | None) -> str:
    """The student's question as the model sees it. A move made on the board is attached with
    the position it was made in, so the tutor knows exactly which position is meant."""
    if move_fen and move_san:
        return f"[학생이 보드에서 둔 수: {move_san} / 그 국면 FEN: {move_fen}]\n{text}"
    return text


# ---------- stream-json -> events ----------


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict))
    return ""


class StreamParser:
    """Turns Claude Code's stream-json lines into UI events. Text is streamed from the partial
    message deltas; tool calls and results come from the full assistant/user messages."""

    def __init__(self, session: ChatSession) -> None:
        self.session = session
        self.text = ""
        self.in_text = False
        self.saw_delta = False
        self.tools: dict[str, str] = {}
        self.tool_started: set[str] = set()

    def feed(self, line: str) -> list[Event]:
        line = line.strip()
        if not line:
            return []
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            log.debug("chat: non-json line from claude: %s", line[:200])
            return []
        if not isinstance(item, dict):
            return []
        kind = item.get("type")
        if kind == "stream_event":
            return self._stream_event(item.get("event") or {})
        if kind == "assistant":
            return self._assistant(item.get("message") or {})
        if kind == "user":
            return self._user(item.get("message") or {})
        if kind == "rate_limit_event":
            return self._limits(item.get("rate_limit_info") or {})
        if kind == "result":
            return self._result(item)
        if kind == "system":
            return self._system(item)
        return []

    def _start_text(self) -> None:
        self.text = ""
        self.in_text = True
        self.saw_delta = False

    def _end_text(self) -> list[Event]:
        if not self.in_text:
            return []
        self.in_text = False
        return [{"type": "text_end", "unverified": self.session.unverified(self.text)}]

    def _stream_event(self, event: dict[str, Any]) -> list[Event]:
        kind = event.get("type")
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "text":
                self._start_text()
                return []
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id", ""))
                name = short_tool_name(str(block.get("name", "")))
                self.tools[tool_id] = name
                self.tool_started.add(tool_id)
                return [{"type": "tool", "id": tool_id, "name": name}]
            return []
        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and self.in_text:
                text = str(delta.get("text", ""))
                self.text += text
                self.saw_delta = True
                return [{"type": "text", "text": text}]
            return []
        if kind == "content_block_stop":
            return self._end_text()
        return []

    def _assistant(self, message: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id", ""))
                name = short_tool_name(str(block.get("name", "")))
                self.tools[tool_id] = name
                if tool_id not in self.tool_started:
                    self.tool_started.add(tool_id)
                    events.append({"type": "tool", "id": tool_id, "name": name})
                events.append(
                    {"type": "tool_args", "id": tool_id, "name": name, "input": block.get("input")}
                )
            elif block.get("type") == "text" and not self.saw_delta:
                # No partial messages arrived for this block (older CLI, or a replayed fake):
                # emit the whole block at once.
                text = str(block.get("text", ""))
                if text:
                    self._start_text()
                    self.text = text
                    events.append({"type": "text", "text": text})
                    events += self._end_text()
        self.saw_delta = False
        return events

    def _user(self, message: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = str(block.get("tool_use_id", ""))
            text = _result_text(block.get("content"))
            ok = not bool(block.get("is_error"))
            if ok:
                self.session.note_squares(text)
            events.append(
                {
                    "type": "tool_result",
                    "id": tool_id,
                    "name": self.tools.get(tool_id, ""),
                    "ok": ok,
                    "preview": text[:RESULT_PREVIEW],
                }
            )
        return events

    def _limits(self, info: dict[str, Any]) -> list[Event]:
        windows = info.get("unifiedWindows") or {}
        five = windows.get("five_hour") or {}
        week = windows.get("seven_day") or {}
        return [
            {
                "type": "limits",
                "status": info.get("status"),
                "five_hour": five.get("utilization"),
                "five_hour_resets_at": five.get("resetsAt"),
                "seven_day": week.get("utilization"),
                "seven_day_resets_at": week.get("resetsAt"),
            }
        ]

    def _result(self, item: dict[str, Any]) -> list[Event]:
        """The CLI's final line. `is_error` is the truth; `subtype` is "success" even on some
        failures (an API error, for instance) and "error_max_turns" when the tool loop hit
        --max-turns, which still produced an answer and is reported as a warning."""
        events = self._end_text()
        is_error = bool(item.get("is_error"))
        subtype = str(item.get("subtype", "")) or "success"
        max_turns = subtype == "error_max_turns"
        ok = max_turns or not is_error
        if is_error and subtype == "success":
            subtype = "error"
        events.append(
            {
                "type": "done",
                "ok": ok,
                "subtype": subtype,
                "duration_ms": item.get("duration_ms"),
                "turns": item.get("num_turns"),
                "cost_usd": item.get("total_cost_usd"),
            }
        )
        if max_turns:
            events.append({"type": "warning", "message": "도구 호출 한도에 걸려 답을 끊었습니다."})
        elif not ok:
            detail = item.get("result") or item.get("error") or subtype
            status = item.get("api_error_status")
            if status:
                detail = f"{detail} (API {status})"
            events.append({"type": "error", "message": f"Claude Code 오류: {detail}"})
        return events

    def _system(self, item: dict[str, Any]) -> list[Event]:
        if item.get("subtype") != "init":
            return []
        if item.get("session_id"):
            # From here on the CLI owns this id: the next turn must --resume, even if this one
            # is cut off by a timeout or a closed tab.
            self.session.resumable = True
        for server in item.get("mcp_servers") or []:
            if isinstance(server, dict) and server.get("name") == SERVER_NAME:
                if server.get("status") != "connected":
                    return [
                        {
                            "type": "warning",
                            "message": f"체스 도구 서버 연결 실패({server.get('status')}). "
                            "답에 보드와 엔진 근거가 빠질 수 있습니다.",
                        }
                    ]
                return []
        return [{"type": "warning", "message": "체스 도구 서버가 로드되지 않았습니다."}]


# ---------- a turn ----------


class TurnRecord:
    """What one answer consisted of, for the chat_turns log."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self.text = ""
        self.done: dict[str, Any] | None = None
        self.errors: list[str] = []

    def add(self, event: Event) -> None:
        kind = event.get("type")
        if kind == "text":
            self.text += str(event.get("text", ""))
        elif kind == "text_end":
            if self.text:
                self.blocks.append(
                    {"type": "text", "text": self.text, "unverified": event.get("unverified", [])}
                )
            self.text = ""
        elif kind == "tool_args":
            self.blocks.append(
                {"type": "tool", "name": event.get("name"), "input": event.get("input")}
            )
        elif kind == "board":
            self.blocks.append(dict(event))
        elif kind == "done":
            self.done = {k: v for k, v in event.items() if k != "type"}
        elif kind in ("error", "warning"):
            self.errors.append(str(event.get("message", "")))

    def content(self) -> dict[str, Any]:
        if self.text:
            self.blocks.append({"type": "text", "text": self.text, "unverified": []})
            self.text = ""
        return {"blocks": self.blocks, "done": self.done, "errors": self.errors}


_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _slots() -> asyncio.Semaphore:
    """Claude Code processes allowed at once. Rebuilt when the event loop changes, which only
    happens in tests (one loop per test); the server has a single loop for its lifetime."""
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(get_settings().chat_concurrency)
        _semaphore_loop = loop
    return _semaphore


async def store_turn(session: ChatSession, role: str, content: dict[str, Any]) -> None:
    try:
        async with session_factory()() as db:
            db.add(
                models.ChatTurn(
                    session_id=session.id,
                    game_id=session.game_id,
                    ply=session.ply,
                    role=role,
                    content=content,
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - logging must never break the chat
        log.warning("chat: could not store %s turn: %s", role, exc)


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the CLI and everything it spawned (it runs in its own process group)."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def _pump(
    proc: asyncio.subprocess.Process, session: ChatSession, message: str, attempt: int
) -> bool:
    """Feed the question, parse stdout into the session queue, end with an `_eof` marker.
    Returns whether a result line was seen. stderr is drained concurrently so a chatty CLI
    can never block on a full pipe."""
    parser = StreamParser(session)
    saw_result = False
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    stderr_task = asyncio.create_task(proc.stderr.read())

    def put(event: Event) -> None:
        event["attempt"] = attempt
        session.queue.put_nowait(event)

    try:
        try:
            proc.stdin.write(message.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the process died before reading; its stderr says why below
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            for event in parser.feed(raw.decode(errors="replace")):
                if event.get("type") == "done":
                    saw_result = True
                put(event)
        stderr_text = (await stderr_task).decode(errors="replace")
        code = await proc.wait()
        if not saw_result:
            lines = [ln for ln in stderr_text.strip().splitlines() if ln.strip()]
            detail = lines[-1] if lines else f"exit code {code}"
            if IN_USE in stderr_text:
                # The id was claimed by an earlier, cut-off turn; the caller retries with
                # --resume once.
                session.resumable = True
                put({"type": "error", "message": detail, "retry": True})
            else:
                put({"type": "error", "message": f"Claude Code가 답을 내지 못했습니다: {detail}"})
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the client, never raised
        log.exception("chat: stream failed")
        put({"type": "error", "message": f"스트림 오류: {exc}"})
    finally:
        if not stderr_task.done():
            stderr_task.cancel()
        put({"type": "_eof"})
    return saw_result


async def _launch(session: ChatSession, message: str) -> AsyncIterator[Event]:
    """One process: its events in order, board events from the tools interleaved, a timeout
    turned into an error, and the process killed whatever way the consumer stops."""
    settings = get_settings()
    session.attempt += 1
    attempt = session.attempt
    _drain(session.queue)
    try:
        proc = await asyncio.create_subprocess_exec(
            *build_command(session),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir()),
            env=subprocess_env(),
            start_new_session=True,
        )
    except OSError as exc:
        yield {"type": "error", "message": f"Claude Code를 시작하지 못했습니다: {exc}"}
        yield {"type": "done", "ok": False, "subtype": "launch_failed"}
        return
    pump = asyncio.create_task(_pump(proc, session, message, attempt))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.chat_timeout_seconds
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            event = await asyncio.wait_for(session.queue.get(), remaining)
            if event.get("attempt") != attempt:
                continue  # left over from a process that was killed
            if event.get("type") == "_eof":
                break
            yield event
    except TimeoutError:
        limit = int(settings.chat_timeout_seconds)
        yield {"type": "error", "message": f"{limit}초 안에 답이 끝나지 않아 중단했습니다."}
        yield {"type": "done", "ok": False, "subtype": "timeout"}
    finally:
        _terminate(proc)
        if not pump.done():
            pump.cancel()
        # asyncio.wait never raises the pump's own CancelledError; a cancellation of *this*
        # task still propagates from the await, as it should.
        await asyncio.wait({pump}, timeout=REAP_SECONDS)
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), REAP_SECONDS)


async def run_turn(
    session: ChatSession, text: str, move_fen: str | None = None, move_san: str | None = None
) -> AsyncIterator[Event]:
    """One question, streamed: the events Claude Code produces plus the board states the tools
    push, in order. Stores both turns in chat_turns. A question that arrives while the previous
    answer is still being written, or while every Claude Code slot is busy, gets an error
    event instead of a silent wait."""
    message = format_message(text, move_fen, move_san)
    try:
        await asyncio.wait_for(session.lock.acquire(), LOCK_WAIT_SECONDS)
    except TimeoutError:
        yield {"type": "error", "message": "이 대화는 아직 이전 답을 쓰는 중입니다."}
        yield {"type": "done", "ok": False, "subtype": "busy"}
        return
    try:
        slots = _slots()
        try:
            await asyncio.wait_for(slots.acquire(), QUEUE_WAIT_SECONDS)
        except TimeoutError:
            yield {
                "type": "error",
                "message": "튜터가 다른 답을 쓰는 중입니다. 잠시 뒤 다시 보내 주세요.",
            }
            yield {"type": "done", "ok": False, "subtype": "busy"}
            return
        try:
            session.last_used = time.monotonic()
            session.note_squares(message)
            await store_turn(
                session, "user", {"text": text, "move_fen": move_fen, "move_san": move_san}
            )
            yield {"type": "session", "session_id": session.id, "resumed": session.resumable}
            record = TurnRecord()
            for attempt in (1, 2):
                retry = False
                async for event in _launch(session, message):
                    if event.get("retry") and attempt == 1:
                        retry = True
                        continue
                    event.pop("retry", None)
                    event.pop("attempt", None)
                    record.add(event)
                    yield event
                if not retry:
                    break
                log.info("chat: session %s was already stored; retrying with --resume", session.id)
            if record.done is not None and record.done.get("ok"):
                session.turns += 1
            await store_turn(session, "assistant", record.content())
        finally:
            slots.release()
    finally:
        session.lock.release()
