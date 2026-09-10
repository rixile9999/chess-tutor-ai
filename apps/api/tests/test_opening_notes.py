"""Deep opening notes: what the server does with what Claude Code wrote, and the two endpoints.

A fake CLI (`tests/fixtures/fake_claude_note.py`) replays one fixed answer, mixed on purpose:
a sentence the verifier confirms, one it refutes, one with no claims at all, a trap line that
plays and one that does not. Every test here is about the server's own work on that answer —
the verification and demotion, the trap replay, the `mine` line from the user's games, the
off-book engine verdict — plus the eight seeded notes.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import func, select

from chess_tutor import db, models
from chess_tutor.config import get_settings
from chess_tutor.schemas import PlayCheckResponse, Score
from chess_tutor.services import opening_notes, play_coach

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_note.py"
RUY = "e4 e5 Nf3 Nc6 Bb5 a6"
OFF_BOOK = "Bc4"
"""Off book after 3...a6, so the note carries an engine verdict (plan §9.2)."""
IN_BOOK = "Ba4"

GAMES: list[tuple[str, str]] = [
    (f"{RUY} {OFF_BOOK} Nf6 O-O Be7 Re1 O-O", "1-0"),
    (f"{RUY} {OFF_BOOK} Nf6 d3 Bc5 O-O O-O", "0-1"),
    (f"{RUY} {OFF_BOOK} Na5 Bxf7+ Kxf7 Nxe5+ Ke8", "0-1"),
    (f"{RUY} {OFF_BOOK} Na5 O-O d6 d4 exd4", "0-1"),
    (f"{RUY} {IN_BOOK} Nf6 O-O Be7 Re1 b5", "1-0"),
]
"""Four games with 4.Bc4 (one win, three losses) and one with the book move, White throughout."""


def board(moves: str = RUY) -> chess.Board:
    b = chess.Board()
    for san in moves.split():
        b.push_san(san)
    return b


def pgn_text(sans: str, result: str) -> str:
    board = chess.Board()
    parts: list[str] = []
    for i, san in enumerate(sans.split()):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.")
        parts.append(san)
        board.push_san(san)
    return " ".join(parts) + " " + result


@pytest.fixture(autouse=True)
def fake_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Every test talks to the fake CLI and logs what it was invoked with."""
    log = tmp_path / "note.log"
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_claude_command", f"{sys.executable} {FAKE}")
    monkeypatch.setattr(settings, "chat_workdir", str(tmp_path / "work"))
    monkeypatch.setattr(settings, "chat_timeout_seconds", 20.0)
    monkeypatch.setenv("FAKE_NOTE_LOG", str(log))
    monkeypatch.delenv("FAKE_NOTE_MODE", raising=False)
    monkeypatch.delenv("FAKE_NOTE_SUMMARY", raising=False)
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


def runs(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


async def seed_games(username: str = "tester") -> None:
    async with db.session_factory()() as session:
        user = models.User(username=username, platform="chesscom")
        session.add(user)
        await session.flush()
        for i, (sans, result) in enumerate(GAMES):
            session.add(
                models.Game(
                    user_id=user.id,
                    source="pgn",
                    source_id=f"{username}-{i}",
                    pgn=pgn_text(sans, result),
                    white=username,
                    black="opp",
                    result=result,
                    user_color="white",
                )
            )
        await session.commit()


async def post(client: AsyncClient, san: str = OFF_BOOK, **extra: Any) -> dict[str, Any]:
    res = await client.post("/openings/note", json={"fen": board().fen(), "san": san, **extra})
    assert res.status_code == 200, res.text
    return dict(res.json())


# ---------- the command ----------


def test_the_command_uses_only_the_chess_tools() -> None:
    command = opening_notes.build_command()
    assert command[:2] == [sys.executable, str(FAKE)]
    assert "-p" in command and "--bare" not in command
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in command
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert allowed == [f"mcp__chess__{name}" for name in opening_notes.TOOL_NAMES]
    assert "mcp__chess__show_board" not in allowed
    config = json.loads(command[command.index("--mcp-config") + 1])
    assert config["mcpServers"]["chess"]["type"] == "http"


def test_the_prompt_carries_the_facts_the_model_may_use() -> None:
    before = board()
    facts = opening_notes.facts_block(before, before.parse_san(OFF_BOOK), 1500)
    assert before.fen() in facts
    assert "4.Bc4" in facts
    assert "책에 있는 수인가: 아니오" in facts
    assert "Bxc6" in facts and "Ba4" in facts  # the book's own continuations
    assert "엔진 라인(깊이 12)" in facts
    prompt = opening_notes.build_prompt(before, before.parse_san(OFF_BOOK), 1500)
    assert facts in prompt and "[[" in prompt and "claims" in prompt


# ---------- writing, storing, reusing ----------


async def test_a_note_is_written_once_and_then_reused(
    aclient: AsyncClient, fake_claude: Path
) -> None:
    first = await post(aclient)
    assert first["position_key"] == "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq -"
    assert first["san"] == OFF_BOOK and first["in_book"] is False
    assert first["model"] == get_settings().chat_model
    assert len(runs(fake_claude)) == 1

    again = await post(aclient)
    assert again["summary"] == first["summary"] and again["created_at"] == first["created_at"]
    assert len(runs(fake_claude)) == 1, "the stored note is returned without asking again"

    async with db.session_factory()() as session:
        rows = (await session.execute(select(func.count(models.OpeningNote.id)))).scalar_one()
    assert rows == 1, "one row per (position, move, language); the second call wrote nothing"


async def test_regenerate_writes_a_new_note_over_the_old_one(
    aclient: AsyncClient, fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await post(aclient)
    monkeypatch.setenv("FAKE_NOTE_SUMMARY", "두 번째 해설입니다.")
    again = await post(aclient, regenerate=True)
    assert first["summary"] != again["summary"]
    assert again["summary"] == "두 번째 해설입니다."
    assert len(runs(fake_claude)) == 2
    stored = await aclient.get("/openings/note", params={"fen": board().fen(), "san": OFF_BOOK})
    assert stored.json()["summary"] == "두 번째 해설입니다."


# ---------- what the server checks ----------


async def test_a_sentence_the_verifier_refutes_keeps_its_text_without_the_marks(
    aclient: AsyncClient,
) -> None:
    note = await post(aclient)
    assert "[[c4 비숍이 f7을 공격합니다.]]" in note["summary"]
    assert "a1 룩이 h8을 공격합니다." in note["summary"]
    assert "[[a1 룩이 h8을 공격합니다.]]" not in note["summary"]
    assert "e4 폰은 이 국면의 열쇠입니다." in note["why"][0], "an unbacked mark is demoted too"
    assert "[[e4 폰은 이 국면의 열쇠입니다.]]" not in note["why"][0]
    assert note["verified_claims"] == 2 and note["total_claims"] == 4


async def test_a_trap_line_that_does_not_play_is_dropped(aclient: AsyncClient) -> None:
    note = await post(aclient)
    assert [trap["title"] for trap in note["traps"]] == ["되잡을 수 없는 폰"]
    trap = note["traps"][0]
    assert trap["line_san"] == ["Nf6", "O-O"]
    assert "[[f3 나이트가 e5 폰을 공격합니다.]]" in trap["text"], "its claim carries its own FEN"


async def test_the_server_writes_the_engine_line_for_an_off_book_move(
    aclient: AsyncClient,
) -> None:
    off_book = await post(aclient)
    assert off_book["engine"] is not None
    assert "inaccuracy" in off_book["engine"] and "Ba4" in off_book["engine"]
    assert "이 줄도 서버가 덮어씁니다." != off_book["engine"]
    in_book = await post(aclient, san=IN_BOOK)
    assert in_book["in_book"] is True and in_book["engine"] is None


async def test_the_server_writes_the_mine_line_from_the_users_own_games(
    aclient: AsyncClient,
) -> None:
    await seed_games()
    note = await post(aclient, username="tester")
    assert note["mine"] is not None
    assert note["mine"].startswith("내 기보 4판 · 승률 25%.")
    assert "Na5" in note["mine"] and "0승 2패" in note["mine"]

    anonymous = await post(aclient, san=IN_BOOK)
    assert anonymous["mine"] is None
    unknown = await post(aclient, san="Bxc6", username="nobody")
    assert unknown["mine"] == opening_notes.NO_GAMES


async def test_pairs_are_serialised_as_san_text_arrays(aclient: AsyncClient) -> None:
    """The web reads replies and alternatives as [SAN, 설명] pairs (plan §9.2)."""
    note = await post(aclient)
    assert note["replies"] == [["Nf6", "e4를 맞공격합니다."], ["b5", "비숍을 다시 쫓습니다."]]
    assert note["alternatives"] == [["Ba4", "책의 메인. 압박을 유지합니다."]]


async def test_sources_say_what_the_note_was_built_from(aclient: AsyncClient) -> None:
    note = await post(aclient)
    assert "llm" in note["sources"] and "book" in note["sources"] and "maia" in note["sources"]


# ---------- reading ----------


async def test_a_move_without_a_note_answers_missing(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/note", params={"fen": board().fen(), "san": "Nc3"})
    assert res.status_code == 200
    assert res.json() == {"status": "missing"}


async def test_reading_refuses_a_move_that_cannot_be_played(aclient: AsyncClient) -> None:
    res = await aclient.get("/openings/note", params={"fen": board().fen(), "san": "Qxh8"})
    assert res.status_code == 422
    res = await aclient.post("/openings/note", json={"fen": "not a fen", "san": "e4"})
    assert res.status_code == 422


# ---------- when Claude Code cannot answer ----------


async def test_a_missing_claude_answers_503_and_stores_nothing(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "chat_claude_command", "no-such-claude-binary")
    res = await aclient.post("/openings/note", json={"fen": board().fen(), "san": OFF_BOOK})
    assert res.status_code == 503
    assert "Claude Code" in res.json()["detail"]
    missing = await aclient.get("/openings/note", params={"fen": board().fen(), "san": OFF_BOOK})
    assert missing.json() == {"status": "missing"}


@pytest.mark.parametrize("mode", ["crash", "error", "prose"])
async def test_an_answer_that_is_not_a_note_answers_503(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("FAKE_NOTE_MODE", mode)
    res = await aclient.post("/openings/note", json={"fen": board().fen(), "san": OFF_BOOK})
    assert res.status_code == 503
    assert res.json()["detail"]


async def test_a_fenced_answer_is_still_read(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_NOTE_MODE", "fenced")
    note = await post(aclient)
    assert note["summary"].startswith("[[c4 비숍이 f7을 공격합니다.]]")


# ---------- the seeded notes ----------


def test_the_seed_is_the_eight_hand_written_ruy_lopez_notes() -> None:
    notes = opening_notes.seed_notes()
    assert len(notes) == 8
    assert [note.san for note in notes] == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bc4", "Ba4"]
    for note in notes:
        assert note.model == opening_notes.SEED_MODEL
        assert note.summary and note.sources[0] == "book"
        assert note.mine is None and note.engine is None, "the server fills these, not the seed"
    bishop = next(note for note in notes if note.san == "Bb5")
    assert "[[b5 비숍이 c6 나이트를 공격합니다.]]" in bishop.summary
    assert bishop.verified_claims == bishop.total_claims == 6
    assert [trap.line_san[0] for trap in bishop.traps] == ["a6", "Nf6"]


def test_a_seeded_sentence_the_board_does_not_back_is_demoted() -> None:
    """1.e4 says the queen is the pawn's only defender; on the board she does not defend it."""
    first = next(note for note in opening_notes.seed_notes() if note.san == "e4")
    assert "[[e4 폰이 d5와 f5를 통제합니다.]]" in first.summary
    assert "e4 폰을 지키는 기물은 퀸뿐입니다." in first.why[1]
    assert "[[e4 폰을 지키는 기물은 퀸뿐입니다.]]" not in first.why[1]
    assert first.verified_claims == 5 and first.total_claims == 6


def test_the_seed_is_loaded_on_startup(client: TestClient) -> None:
    """The app's lifespan stores them, so the page has notes without Claude Code installed."""
    res = client.get("/openings/note", params={"fen": chess.Board().fen(), "san": "e4"})
    assert res.status_code == 200
    assert res.json()["model"] == opening_notes.SEED_MODEL
    assert res.json()["summary"].startswith("[[e4 폰이 d5와 f5를 통제합니다.]]")


async def test_loading_the_seed_twice_stores_it_once() -> None:
    async with db.session_factory()() as session:
        assert await opening_notes.load_seed(session) == 8
        assert await opening_notes.load_seed(session) == 0
        rows = (await session.execute(select(func.count(models.OpeningNote.id)))).scalar_one()
    assert rows == 8


async def test_a_written_note_is_not_overwritten_by_the_seed(aclient: AsyncClient) -> None:
    written = await post(aclient, san=IN_BOOK)
    async with db.session_factory()() as session:
        assert await opening_notes.load_seed(session) == 7, "every seeded note but this one"
    stored = await aclient.get("/openings/note", params={"fen": board().fen(), "san": IN_BOOK})
    assert stored.json()["summary"] == written["summary"]
    assert stored.json()["model"] != opening_notes.SEED_MODEL
