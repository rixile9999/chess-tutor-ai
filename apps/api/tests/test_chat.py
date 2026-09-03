"""Position chat: the Claude Code subprocess wrapper (with a fake `claude` that replays a
recorded stream-json answer), the stream parser, the board-event merge, the chess tools the
chat exposes over MCP, the system prompt, and the SSE endpoint."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db, models, schemas
from chess_tutor.config import get_settings
from chess_tutor.engine import find_stockfish
from chess_tutor.services import chat as chat_svc
from chess_tutor.services import chat_prompt, chat_tools

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")

FAKE = Path(__file__).parent / "fixtures" / "fake_claude.py"
FEN = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
AFTER = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 5 21"
START = chess.STARTING_FEN
PGN = (
    '[Event "mockup"]\n[White "a"]\n[Black "b"]\n[Result "1-0"]\n[SetUp "1"]\n'
    f'[FEN "{FEN}"]\n\n20... Qd7 21. Nxf6+ Bxf6 22. Rxd7 1-0'
)
DEPTH = 8


@pytest.fixture(autouse=True)
def _fake_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Every test talks to the fake CLI, logs its invocations, and keeps sessions in tmp."""
    log = tmp_path / "claude.log"
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_claude_command", f"{sys.executable} {FAKE}")
    monkeypatch.setattr(settings, "chat_workdir", str(tmp_path / "work"))
    monkeypatch.setattr(settings, "chat_timeout_seconds", 20.0)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("FAKE_CLAUDE_PAUSE", raising=False)
    chat_svc._sessions.clear()
    yield log
    chat_svc._sessions.clear()


def _invocations(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


async def _collect(session: chat_svc.ChatSession, text: str, **kw: Any) -> list[dict[str, Any]]:
    return [event async for event in chat_svc.run_turn(session, text, **kw)]


def _session(prompt: str = "facts: Nd5 is strong") -> chat_svc.ChatSession:
    return chat_svc.create_session(1, 40, 1500, prompt, FEN, AFTER)


# ---------- command and environment ----------


def test_build_command_uses_only_the_chess_tools() -> None:
    session = _session()
    command = chat_svc.build_command(session)
    assert command[:2] == [sys.executable, str(FAKE)]
    assert "-p" in command
    assert "--bare" not in command
    assert command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert allowed == chat_svc.full_tool_names()
    assert "mcp__chess__show_board" in allowed
    config = json.loads(command[command.index("--mcp-config") + 1])
    server = config["mcpServers"]["chess"]
    assert server["type"] == "http" and server["headers"]["X-Chat-Session"] == session.id
    assert command[command.index("--session-id") + 1] == session.id
    assert "--resume" not in command
    session.resumable = True
    resumed = chat_svc.build_command(session)
    assert resumed[resumed.index("--resume") + 1] == session.id
    assert "--session-id" not in resumed


def test_subprocess_env_drops_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    env = chat_svc.subprocess_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_availability_reports_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    assert chat_svc.availability()["available"] is True
    monkeypatch.setattr(get_settings(), "chat_claude_command", "no-such-claude-binary")
    info = chat_svc.availability()
    assert info["available"] is False and info["reason"]


def test_format_message_attaches_the_board_move() -> None:
    assert chat_svc.format_message("왜?", None, None) == "왜?"
    text = chat_svc.format_message("왜?", FEN, "Nxd5")
    assert text.startswith("[학생이 보드에서 둔 수: Nxd5 / 그 국면 FEN: ") and text.endswith(
        "\n왜?"
    )


# ---------- stream parser ----------


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj)


def test_parser_turns_stream_json_into_events() -> None:
    session = _session("Nd5")
    parser = chat_svc.StreamParser(session)
    assert parser.feed("not json") == []
    assert parser.feed(_line({"type": "system", "subtype": "init", "mcp_servers": []})) == [
        {"type": "warning", "message": "체스 도구 서버가 로드되지 않았습니다."}
    ]
    start = {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}
    assert parser.feed(_line({"type": "stream_event", "event": start})) == []
    delta = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "f6 폰"}}
    assert parser.feed(_line({"type": "stream_event", "event": delta})) == [
        {"type": "text", "text": "f6 폰"}
    ]
    stop = {"type": "content_block_stop", "index": 0}
    assert parser.feed(_line({"type": "stream_event", "event": stop})) == [
        {"type": "text_end", "unverified": ["f6"]}
    ]
    tool = {
        "type": "tool_use",
        "id": "t1",
        "name": "mcp__chess__analyse",
        "input": {"fen": FEN},
    }
    events = parser.feed(_line({"type": "assistant", "message": {"content": [tool]}}))
    assert events == [
        {"type": "tool", "id": "t1", "name": "analyse"},
        {"type": "tool_args", "id": "t1", "name": "analyse", "input": {"fen": FEN}},
    ]
    result = {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": [{"type": "text", "text": '{"best": "Nxf6+"}'}],
    }
    events = parser.feed(_line({"type": "user", "message": {"content": [result]}}))
    assert events[0]["type"] == "tool_result" and events[0]["ok"] and events[0]["name"] == "analyse"
    assert "f6" in session.known_squares  # grounded by the tool result
    done = parser.feed(
        _line({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3})
    )
    assert done == [
        {
            "type": "done",
            "ok": True,
            "subtype": "success",
            "duration_ms": None,
            "turns": 3,
            "cost_usd": None,
        }
    ]


def test_parser_reports_failed_tool_and_error_result() -> None:
    parser = chat_svc.StreamParser(_session())
    failed = {"type": "tool_result", "tool_use_id": "t9", "content": "boom h3", "is_error": True}
    events = parser.feed(_line({"type": "user", "message": {"content": [failed]}}))
    assert events == [
        {"type": "tool_result", "id": "t9", "name": "", "ok": False, "preview": "boom h3"}
    ]
    assert "h3" not in parser.session.known_squares
    events = parser.feed(
        _line({"type": "result", "subtype": "error", "is_error": True, "result": "x"})
    )
    assert events[0]["type"] == "done" and not events[0]["ok"]
    assert events[1] == {"type": "error", "message": "Claude Code 오류: x"}


def test_parser_emits_whole_text_block_without_partial_messages() -> None:
    parser = chat_svc.StreamParser(_session())
    block = {"type": "text", "text": "Nxd5가 최선입니다."}
    events = parser.feed(_line({"type": "assistant", "message": {"content": [block]}}))
    assert events == [
        {"type": "text", "text": "Nxd5가 최선입니다."},
        {"type": "text_end", "unverified": []},
    ]


# ---------- a turn against the fake CLI ----------


async def test_turn_streams_text_tools_and_result(_fake_claude: Path) -> None:
    session = _session("facts: Nd5")
    events = await _collect(session, "왜 Nf5는 안 되나요?")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "session" and events[0]["session_id"] == session.id
    assert not events[0]["resumed"]
    assert kinds.count("text_end") == 2
    assert "tool" in kinds and "tool_args" in kinds and "tool_result" in kinds
    assert kinds.index("tool_args") < kinds.index("tool_result")
    assert "limits" in kinds and kinds[-1] == "done" and events[-1]["ok"]
    text = "".join(e["text"] for e in events if e["type"] == "text")
    assert text.startswith("Nf5는 블런더입니다.")
    first, second = [e for e in events if e["type"] == "text_end"]
    # d5 is in the facts, f5 in the question; f6 is only grounded once the tool result names it.
    assert first["unverified"] == ["f6"]
    assert second["unverified"] == ["h3"]
    args = _invocations(_fake_claude)[0]
    assert "--session-id" in args["args"] and args["prompt"] == "왜 Nf5는 안 되나요?"
    assert session.resumable and session.turns == 1


async def test_second_turn_resumes_the_stored_session(_fake_claude: Path) -> None:
    session = _session()
    await _collect(session, "첫 질문")
    events = await _collect(session, "둘째 질문", move_fen=FEN, move_san="Nxd5")
    assert events[0]["resumed"] is True
    runs = _invocations(_fake_claude)
    assert "--session-id" in runs[0]["args"] and "--resume" in runs[1]["args"]
    assert runs[1]["args"][runs[1]["args"].index("--resume") + 1] == session.id
    assert runs[1]["prompt"].startswith("[학생이 보드에서 둔 수: Nxd5")


async def test_board_events_merge_into_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """A show_board call arrives from the MCP side while the CLI is between events; it must
    appear in the stream at that moment, not at the end."""
    monkeypatch.setenv("FAKE_CLAUDE_PAUSE", "0.5")
    session = _session()
    kinds: list[str] = []
    async for event in chat_svc.run_turn(session, "q"):
        kinds.append(event["type"])
        if event["type"] == "tool_args":
            chat_tools.show_board_impl(session, AFTER, ["Nxf6+"], "체크")
    assert "board" in kinds
    assert kinds.index("tool_args") < kinds.index("board") < kinds.index("tool_result")
    assert "d5" in session.known_squares and "f6" in session.known_squares


async def test_crash_is_reported_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "crash")
    session = _session()
    events = await _collect(session, "q")
    assert events[-1]["type"] == "error" and "boom" in events[-1]["message"]
    assert not session.resumable


async def test_missing_binary_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "chat_claude_command", "/nonexistent/claude")
    events = await _collect(_session(), "q")
    assert events[1]["type"] == "error" and events[-1] == {
        "type": "done",
        "ok": False,
        "subtype": "launch_failed",
    }


async def test_mcp_failure_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "no-mcp")
    events = await _collect(_session(), "q")
    warnings = [e for e in events if e["type"] == "warning"]
    assert warnings and "연결 실패" in warnings[0]["message"]


async def test_turns_are_stored() -> None:
    session = _session()
    await _collect(session, "왜요?")
    async with db.session_factory()() as s:
        rows = (
            (await s.execute(select(models.ChatTurn).order_by(models.ChatTurn.id))).scalars().all()
        )
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content["text"] == "왜요?" and rows[0].session_id == session.id
    blocks = rows[1].content["blocks"]
    assert [b["type"] for b in blocks] == ["text", "tool", "text"]
    assert blocks[1]["name"] == "show_board" and rows[1].content["done"]["ok"]


# ---------- the tools ----------


def test_show_board_plays_moves_and_pushes_an_event() -> None:
    session = _session()
    out = chat_tools.show_board_impl(
        session, START, ["e4", "e5", "Nf3"], "나이트 전개", ["g1f3:good", "e5-e4"], ["e5"]
    )
    assert out.shown and out.moves == ["e4", "e5", "Nf3"] and out.last_move == ["g1", "f3"]
    event = session.queue.get_nowait()
    assert event is not None and event["type"] == "board" and event["n"] == 1
    assert event["fen"] == out.fen and event["caption"] == "나이트 전개"
    assert event["arrows"][0] == {"orig": "g1", "dest": "f3", "color": "good", "dashed": False}
    assert event["arrows"][1]["color"] == "ink" and event["highlights"] == ["e5"]
    assert "f3" in session.known_squares


def test_show_board_without_a_session_reports_it() -> None:
    out = chat_tools.show_board_impl(None, START, ["e4"], "")
    assert not out.shown and out.fen.startswith("rnbqkbnr/pppppppp/8/8/4P3")


def test_show_board_rejects_illegal_input() -> None:
    session = _session()
    with pytest.raises(ValueError, match="합법 수가 아닙니다"):
        chat_tools.show_board_impl(session, START, ["e5"], "")
    with pytest.raises(ValueError, match="화살표"):
        chat_tools.show_board_impl(session, START, [], "", ["zz"])
    with pytest.raises(ValueError, match="칸 이름"):
        chat_tools.show_board_impl(session, START, [], "", [], ["e9"])
    with pytest.raises(ValueError, match="FEN"):
        chat_tools.show_board_impl(session, "garbage", [], "")
    assert session.queue.empty()


def test_motifs_tool_finds_the_fork() -> None:
    out = chat_tools.motifs_impl(AFTER, "Nxf6+")
    kinds = {m.kind for m in out.motifs}
    assert out.fen_after.startswith("5rk1/p2qbppp/1p2pN2")
    assert kinds & {"fork", "discovered_attack", "remove_defender"}


def test_features_tool_reports_structure_and_rows() -> None:
    out = chat_tools.features_impl(FEN)
    assert out.side_to_move == "black"
    assert out.rows and {r.feature for r in out.rows}
    assert out.structure is None or out.structure.key


def test_maia_probs_tool_orders_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    out = chat_tools.maia_probs_impl(START, 1200, ["e4"])
    assert out.rating == 1200 and "e4" in out.probs
    values = list(out.probs.values())
    assert values == sorted(values, reverse=True)


@needs_engine
async def test_analyse_tool_uses_the_engine_cache() -> None:
    out = await chat_tools.analyse_impl(AFTER, depth=DEPTH, multipv=2)
    assert out.side_to_move == "white" and out.depth == DEPTH
    assert out.best == "Nxf6+" and out.lines[0].pv[0] == "Nxf6+"
    assert 0.0 < out.lines[0].win_prob_mover <= 1.0
    again = await chat_tools.analyse_impl(AFTER, depth=DEPTH, multipv=2)
    assert again == out


@needs_engine
async def test_compare_tool_ranks_the_blunder_below_the_capture() -> None:
    out = await chat_tools.compare_impl(FEN, "Nxd5", "Qd7", depth=DEPTH)
    assert out.side_to_move == "black" and out.better == "Nxd5"
    assert out.a.loss_vs_better == 0.0 and out.a.classification == "best"
    assert out.b.classification in ("mistake", "blunder")
    assert out.b.reply_line[0] == "Nxf6+" and out.b.loss_vs_better > 0.1
    assert out.b.fen_after == AFTER
    with pytest.raises(ValueError, match="합법 수"):
        await chat_tools.compare_impl(FEN, "Nxd5", "Ke1")


def test_depth_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "chat_max_depth", 12)
    assert chat_tools._depth(30) == 12 and chat_tools._depth(None) <= 12
    assert chat_tools._depth(1) == 4


# ---------- prompt ----------


def _review() -> schemas.MoveReviewOut:
    return schemas.MoveReviewOut(
        game_id=1,
        ply=40,
        san="Qd7",
        color="black",
        fen_before=FEN,
        fen_after=AFTER,
        classification="blunder",
        eval_before=schemas.Score(cp=-30),
        eval_after=schemas.Score(cp=590),
        refutation=schemas.Refutation(main_line=["Nxf6+", "Bxf6", "Rxd7"]),
        alternatives=[
            schemas.Alternative(
                san="Nxd5",
                eval=schemas.Score(cp=-30),
                line=["exd5"],
                is_best=True,
                why="나이트를 잡습니다.",
                claims=[],
            )
        ],
        explanation=schemas.Explanation(
            headline="20… Qd7는 블런더", lead="퀸이 나이트 포크에 걸립니다.", verified=True
        ),
    )


def _analysis() -> schemas.GameAnalysis:
    moves = [
        schemas.MoveAnalysis(
            ply=40,
            san="Qd7",
            uci="c6d7",
            color="black",
            fen_before=FEN,
            fen_after=AFTER,
            eval_before=schemas.Score(cp=-30),
            eval_after=schemas.Score(cp=590),
            classification="blunder",
        ),
        schemas.MoveAnalysis(
            ply=41,
            san="Nxf6+",
            uci="d5f6",
            color="white",
            fen_before=AFTER,
            fen_after=AFTER,
            eval_before=schemas.Score(cp=590),
            eval_after=schemas.Score(cp=590),
            classification="best",
        ),
    ]
    return schemas.GameAnalysis(game_id=1, status="done", depth=DEPTH, moves=moves)


def test_prompt_carries_the_facts_without_claims() -> None:
    game = models.Game(pgn=PGN, white="a", black="b", result="1-0", user_color="black")
    prompt = chat_prompt.build_system_prompt(game, _analysis(), _review(), 1500)
    assert prompt.startswith(chat_prompt.ROLE)
    body = prompt[prompt.rindex("<facts>") + len("<facts>") : prompt.rindex("</facts>")]
    facts = json.loads(body)
    assert facts["move"]["label"] == "20… Qd7" and facts["move"]["engine_best"] == "Nxd5"
    assert facts["positions"]["before"] == FEN and facts["positions"]["after"] == AFTER
    assert facts["positions"]["punished"].startswith("5rk1/p2qbppp/1p2pN2")
    assert facts["moves_before"] == "20... Qd7" and facts["moves_after"] == "21. Nxf6+"
    assert facts["game"]["student_color"] == "black"
    assert "claims" not in body
    assert facts["alternatives"][0]["why"] == "나이트를 잡습니다."


def test_moves_text_numbers_plies() -> None:
    moves = _analysis().moves
    assert chat_prompt.moves_text(moves, 40, 41) == "20... Qd7 21. Nxf6+"
    assert chat_prompt.moves_text(moves, 41, 41) == "21. Nxf6+"
    assert chat_prompt.moves_text(moves, 1, 39) == ""


# ---------- endpoints ----------


def _events(body: str) -> list[dict[str, Any]]:
    return [json.loads(chunk[6:]) for chunk in body.split("\n\n") if chunk.startswith("data: ")]


async def test_status_endpoint(aclient: AsyncClient) -> None:
    r = await aclient.get("/chat/status")
    assert r.status_code == 200
    assert r.json()["available"] is True and r.json()["model"] == get_settings().chat_model


async def test_chat_endpoint_404s(aclient: AsyncClient) -> None:
    r = await aclient.post("/review/999/1/chat", json={"message": "?"})
    assert r.status_code == 404


async def _insert_game() -> int:
    async with db.session_factory()() as session:
        game = models.Game(
            source="pgn", source_id="mockup", pgn=PGN, white="a", black="b", result="1-0"
        )
        session.add(game)
        await session.commit()
        return game.id


@needs_engine
async def test_chat_endpoint_streams_and_resumes(aclient: AsyncClient, _fake_claude: Path) -> None:
    game_id = await _insert_game()
    r = await aclient.post(
        f"/review/{game_id}/1/chat?depth={DEPTH}", json={"message": "왜 Qd7가 블런더죠?"}
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0]["type"] == "session" and events[-1]["type"] == "done"
    session_id = events[0]["session_id"]
    session = chat_svc.get_session(session_id)
    assert session is not None and session.game_id == game_id and session.ply == 1
    assert "<facts>" in session.system_prompt and "20… Qd7" in session.system_prompt
    # Squares the facts mention are grounded from the start.
    assert "d5" in session.known_squares
    r = await aclient.post(
        f"/review/{game_id}/1/chat?depth={DEPTH}",
        json={
            "message": "그럼 Nxd5는요?",
            "session_id": session_id,
            "move": {"fen": FEN, "san": "Nxd5"},
        },
    )
    events = _events(r.text)
    assert events[0]["session_id"] == session_id and events[0]["resumed"] is True
    runs = _invocations(_fake_claude)
    assert len(runs) == 2 and "--resume" in runs[1]["args"]
    assert runs[1]["prompt"].startswith("[학생이 보드에서 둔 수: Nxd5")
    # A session id for another ply starts a fresh conversation instead of mixing them.
    r = await aclient.post(
        f"/review/{game_id}/2/chat?depth={DEPTH}",
        json={"message": "이 수는요?", "session_id": session_id},
    )
    events = _events(r.text)
    assert events[0]["session_id"] != session_id and events[0]["resumed"] is False


def test_mcp_mount_lists_the_tools(client: TestClient) -> None:
    """Through the lifespan (TestClient runs it; the ASGI transport does not), so the MCP
    session manager is running."""
    headers = {"Accept": "application/json, text/event-stream"}
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    r = client.post("/mcp/", json=init, headers=headers)
    assert r.status_code == 200
    r = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
    )
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert set(names) == set(chat_svc.TOOL_NAMES)
    session = _session()
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "show_board",
            "arguments": {"fen": START, "moves": ["d4"], "caption": "c"},
        },
    }
    r = client.post("/mcp/", json=call, headers={**headers, "X-Chat-Session": session.id})
    result = r.json()["result"]
    assert not result.get("isError") and result["structuredContent"]["shown"] is True
    event = session.queue.get_nowait()
    assert event is not None and event["moves"] == ["d4"]
    bad = {
        **call,
        "id": 4,
        "params": {"name": "show_board", "arguments": {"fen": START, "moves": ["d5"]}},
    }
    r = client.post("/mcp/", json=bad, headers={**headers, "X-Chat-Session": session.id})
    result = r.json()["result"]
    assert result.get("isError") and "합법 수" in result["content"][0]["text"]


def test_fake_claude_is_importable() -> None:
    """The fixture doubles as documentation of the stream-json shapes; keep it valid."""
    assert FAKE.exists() and os.access(FAKE, os.R_OK)
