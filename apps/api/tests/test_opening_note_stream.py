"""Streamed opening notes (plan §10.2): what the client sees while a note is being written.

A fake CLI (`tests/fixtures/fake_claude_note_stream.py`) replays a real stream-json run whose
text deltas are cut at arbitrary offsets, so the server has to reassemble the NDJSON lines
itself. The same fake answers the one-shot way when the command line asks for it, which is how
the 20-second fallback is exercised. Every test here is about the stream's own contract: the
order of the events, the per-section verification, what is stored and what is not.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from chess_tutor import db, models
from chess_tutor.config import get_settings
from chess_tutor.schemas import NoteRequest, PlayCheckResponse, Score
from chess_tutor.services import opening_notes, play_coach

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_note_stream.py"
RUY = "e4 e5 Nf3 Nc6 Bb5 a6"
OFF_BOOK = "Bc4"
IN_BOOK = "Ba4"


def board(moves: str = RUY) -> chess.Board:
    b = chess.Board()
    for san in moves.split():
        b.push_san(san)
    return b


@pytest.fixture(autouse=True)
def fake_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    log = tmp_path / "note.log"
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_claude_command", f"{sys.executable} {FAKE}")
    monkeypatch.setattr(settings, "chat_workdir", str(tmp_path / "work"))
    monkeypatch.setattr(settings, "chat_timeout_seconds", 20.0)
    monkeypatch.setenv("FAKE_NOTE_LOG", str(log))
    for name in ("FAKE_STREAM_MODE", "FAKE_STREAM_HANG", "FAKE_NOTE_MODE", "FAKE_NOTE_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    yield log


@pytest.fixture(autouse=True)
def stub_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Stockfish: the facts block and the off-book verdict get fixed answers."""

    def check(req: Any) -> PlayCheckResponse:
        return PlayCheckResponse(
            san=str(req.san),
            uci="0000",
            classification="inaccuracy",
            win_loss=0.031,
            eval_before=Score(cp=25),
            eval_after=Score(cp=-5),
            best_san="Ba4",
            best_uci="b5a4",
        )

    monkeypatch.setattr(play_coach, "check", check)
    monkeypatch.setattr(opening_notes, "_engine_lines", lambda _board: ["1. Ba4 Nf6 (+0.3)"])


def events(text: str) -> list[dict[str, Any]]:
    """The SSE frames of a finished stream, as objects."""
    out: list[dict[str, Any]] = []
    for frame in text.split("\n\n"):
        data = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


async def stream(client: AsyncClient, san: str = OFF_BOOK, **extra: Any) -> list[dict[str, Any]]:
    res = await client.post(
        "/openings/note/stream", json={"fen": board().fen(), "san": san, **extra}
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")
    return events(res.text)


def only(evs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in evs if e["type"] == kind]


async def rows() -> int:
    async with db.session_factory()() as session:
        return int((await session.execute(select(func.count(models.OpeningNote.id)))).scalar_one())


# ---------- the command ----------


def test_the_streamed_command_asks_for_partial_messages() -> None:
    command = opening_notes.build_command(stream=True)
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in command and "--verbose" in command
    assert "--bare" not in command and command[command.index("--tools") + 1] == ""
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert allowed == [f"mcp__chess__{name}" for name in opening_notes.TOOL_NAMES]
    # The one-shot command is untouched: the seed and POST /openings/note still use it.
    assert (
        opening_notes.build_command()[opening_notes.build_command().index("--output-format") + 1]
        == "json"
    )


def test_the_streamed_prompt_asks_for_one_json_line_per_section() -> None:
    before = board()
    facts = opening_notes.facts_block(before, before.parse_san(OFF_BOOK), 1500)
    prompt = opening_notes.build_stream_prompt(facts)
    assert facts in prompt
    for name in opening_notes.SECTIONS:
        assert f'"section": "{name}"' in prompt
    assert "한 줄에 완성된 JSON 객체 하나씩" in prompt
    assert "[[" in prompt and "claims" in prompt


# ---------- the events ----------


async def test_the_stream_reports_stages_then_sections_then_the_note(aclient: AsyncClient) -> None:
    evs = await stream(aclient)
    kinds = [e["type"] for e in evs]
    assert kinds[: len(opening_notes.STAGES)] == ["stage"] * len(opening_notes.STAGES)
    assert [e["name"] for e in only(evs, "stage")] == list(opening_notes.STAGES)
    assert all(e["detail"] for e in only(evs, "stage")), "every stage says what it found"
    assert kinds[-1] == "note"

    names = [e["name"] for e in only(evs, "section")]
    # The server's own sections come first, then the model's in the order of the prompt.
    assert names[0] == "engine", "off the book, so the engine verdict is the first section"
    assert names[1:] == list(opening_notes.SECTIONS)

    tools = only(evs, "tool")
    assert [t["name"] for t in tools] == ["analyse"], "the model's tool calls flow through"
    assert tools[0]["input"]["depth"] == 12


async def test_each_section_is_verified_on_arrival(aclient: AsyncClient) -> None:
    """The demotion happens per section, before the note exists (plan §10.2)."""
    evs = await stream(aclient)
    sections = {e["name"]: e for e in only(evs, "section")}

    summary = sections["summary"]
    assert "[[c4 비숍이 f7을 공격합니다.]]" in summary["payload"]
    assert "[[a1 룩이 h8을 공격합니다.]]" not in summary["payload"]
    assert "a1 룩이 h8을 공격합니다." in summary["payload"]
    assert (summary["verified_claims"], summary["total_claims"]) == (1, 2)

    why = sections["why"]
    assert "[[e4 폰은 이 국면의 열쇠입니다.]]" not in why["payload"][0], "an unbacked mark too"
    assert (why["verified_claims"], why["total_claims"]) == (0, 1)

    traps = sections["traps"]
    assert [t["title"] for t in traps["payload"]] == ["되잡을 수 없는 폰"], "illegal line dropped"
    assert (traps["verified_claims"], traps["total_claims"]) == (1, 1)

    assert sections["replies"]["payload"] == [
        ["Nf6", "e4를 맞공격합니다."],
        ["b5", "비숍을 다시 쫓습니다."],
    ]
    assert sections["engine"]["payload"].startswith("엔진(깊이 12)")
    assert sections["engine"]["total_claims"] == 0, "the server's own line is not a model claim"


async def test_the_note_event_is_what_get_note_returns(aclient: AsyncClient) -> None:
    evs = await stream(aclient)
    streamed = only(evs, "note")[0]["note"]
    stored = await aclient.get("/openings/note", params={"fen": board().fen(), "san": OFF_BOOK})
    assert stored.status_code == 200
    assert stored.json() == streamed
    assert streamed["verified_claims"] == 2 and streamed["total_claims"] == 4
    assert streamed["model"] == get_settings().chat_model
    assert streamed["engine"] is not None and streamed["in_book"] is False
    assert await rows() == 1


async def test_the_sections_add_up_to_the_note(aclient: AsyncClient) -> None:
    """What the panel drew while streaming is what it gets when it reloads the page."""
    evs = await stream(aclient)
    sections = {e["name"]: e["payload"] for e in only(evs, "section")}
    note = only(evs, "note")[0]["note"]
    assert note["summary"] == sections["summary"]
    assert note["why"] == sections["why"]
    assert note["replies"] == [list(pair) for pair in sections["replies"]]
    assert [t["title"] for t in note["traps"]] == [t["title"] for t in sections["traps"]]
    assert note["engine"] == sections["engine"]


async def test_a_stored_note_is_sent_back_without_calling_claude(
    aclient: AsyncClient, fake_claude: Path
) -> None:
    first = await stream(aclient)
    again = await stream(aclient)
    assert [e["type"] for e in again] == ["note"]
    assert again[0]["note"] == only(first, "note")[0]["note"]
    assert len(fake_claude.read_text().splitlines()) == 1, "one CLI run, not two"

    forced = await stream(aclient, regenerate=True)
    assert only(forced, "stage"), "regenerate really writes it again"
    assert len(fake_claude.read_text().splitlines()) == 2


async def test_the_mine_section_comes_from_the_users_own_games(aclient: AsyncClient) -> None:
    from tests.test_opening_notes import seed_games

    await seed_games()
    evs = await stream(aclient, username="tester")
    mine = next(e for e in only(evs, "section") if e["name"] == "mine")
    assert mine["payload"].startswith("내 기보 4판 · 승률 25%.")
    # It arrives before anything the model wrote.
    names = [e["name"] for e in only(evs, "section")]
    assert names.index("mine") < names.index("summary")


# ---------- stopping, and not storing ----------


async def test_an_aborted_stream_stores_nothing() -> None:
    """The client going away cancels the generator; the note is never assembled (plan §10.2)."""
    before = board()
    async with db.session_factory()() as session:
        events_seen: list[dict[str, Any]] = []
        gen = opening_notes.stream(session, NoteRequest(fen=before.fen(), san=OFF_BOOK))
        async for event in gen:
            events_seen.append(event)
            if event["type"] == "section" and event["name"] == "summary":
                break  # the student pressed 중단
        await gen.aclose()
    assert [e["type"] for e in events_seen][-1] == "section"
    assert not any(e["type"] == "note" for e in events_seen)
    assert await rows() == 0


async def test_prose_in_front_of_the_first_line_does_not_cost_the_summary(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured against the real CLI: it likes to say what it is about to do on that line, and
    to break a long string over two lines. Neither may lose a section (plan §10.2)."""
    monkeypatch.setenv("FAKE_STREAM_MODE", "messy")
    evs = await stream(aclient)
    assert [e["name"] for e in only(evs, "section")] == ["engine", *opening_notes.SECTIONS]
    summary = next(e for e in only(evs, "section") if e["name"] == "summary")
    assert summary["payload"].startswith("[[c4 비숍이 f7을 공격합니다.]]")
    assert "확인했습니다" not in summary["payload"]
    assert only(evs, "note")[0]["note"]["why"], "the line split by a newline survived too"


async def test_an_answer_that_breaks_off_is_not_stored(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_STREAM_MODE", "partial")
    evs = await stream(aclient)
    assert [e["name"] for e in only(evs, "section")] == ["engine", "summary"]
    assert not only(evs, "note")
    assert only(evs, "warning")[-1]["message"] == opening_notes.INCOMPLETE
    assert await rows() == 0


async def test_a_crash_before_any_section_falls_back_once(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_STREAM_MODE", "crash")
    evs = await stream(aclient)
    assert [e["name"] for e in only(evs, "section")] == ["engine", *opening_notes.SECTIONS]
    assert only(evs, "note"), "the one-shot retry finished the note"


async def test_a_missing_claude_ends_the_stream_with_an_error(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "chat_claude_command", "no-such-claude-binary")
    evs = await stream(aclient)
    assert only(evs, "error"), "reported in the stream, not as a 503 after the headers went out"
    assert "Claude Code" in only(evs, "error")[0]["message"]
    assert await rows() == 0


# ---------- the 20-second safety net ----------


async def test_a_model_that_says_nothing_falls_back_to_the_one_shot_path(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch, fake_claude: Path
) -> None:
    """No section within FIRST_SECTION_SECONDS: kill the process, ask once the old way."""
    monkeypatch.setenv("FAKE_STREAM_MODE", "hang")
    monkeypatch.setenv("FAKE_STREAM_HANG", "30")
    monkeypatch.setattr(opening_notes, "FIRST_SECTION_SECONDS", 0.6)
    evs = await stream(aclient)
    assert opening_notes.NO_SECTIONS in [e["message"] for e in only(evs, "warning")]
    assert [e["name"] for e in only(evs, "section")] == ["engine", *opening_notes.SECTIONS]
    note = only(evs, "note")[0]["note"]
    assert note["summary"].startswith("[[c4 비숍이 f7을 공격합니다.]]")
    assert note["verified_claims"] == 2 and note["total_claims"] == 4

    runs = [json.loads(line) for line in fake_claude.read_text().splitlines()]
    assert len(runs) == 2, "the streamed run, then exactly one retry"
    assert "stream-json" in runs[0]["args"] and "stream-json" not in runs[1]["args"]
    assert await rows() == 1


async def test_prose_instead_of_ndjson_falls_back_too(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run ends normally but never wrote a section; no need to wait out the deadline."""
    monkeypatch.setenv("FAKE_STREAM_MODE", "prose")
    evs = await stream(aclient)
    assert opening_notes.NO_SECTIONS in [e["message"] for e in only(evs, "warning")]
    assert only(evs, "note")
    assert await rows() == 1


# ---------- the position is still checked first ----------


async def test_a_move_that_cannot_be_played_is_refused_before_the_stream(
    aclient: AsyncClient,
) -> None:
    res = await aclient.post("/openings/note/stream", json={"fen": board().fen(), "san": "Qxh8"})
    assert res.status_code == 422
    res = await aclient.post("/openings/note/stream", json={"fen": "not a fen", "san": "e4"})
    assert res.status_code == 422


async def test_an_in_book_move_has_no_engine_section(aclient: AsyncClient) -> None:
    evs = await stream(aclient, san=IN_BOOK)
    assert [e["name"] for e in only(evs, "section")] == list(opening_notes.SECTIONS)
    assert only(evs, "note")[0]["note"]["in_book"] is True
