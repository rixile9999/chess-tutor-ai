"""Asking the tutor about a note (plan §10.3): the note in the prompt, the question chips the
note makes by itself, and the answers the student keeps on it.

The chat itself is the M7 live chat — same endpoint, same stream, same fake CLI — so what is
tested here is only what the note adds: the 지금 보는 해설 block, the conversation being keyed
by the move rather than by the position, and the addendum round trip.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from chess_tutor.config import get_settings
from chess_tutor.schemas import OpeningContext, OpeningNote, TrapLine
from chess_tutor.services import chat as chat_svc
from chess_tutor.services import chat_prompt, opening_notes

FAKE = Path(__file__).parent / "fixtures" / "fake_claude.py"
RUY = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
BEFORE = "r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
"""After 3...a6: White is to move and plays 4.Ba4 or 4.Bxc6."""


def fen_after(sans: list[str]) -> str:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.fen()


def note(san: str = "Ba4", *, in_book: bool = True) -> OpeningNote:
    return OpeningNote(
        position_key="r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq -",
        san=san,
        in_book=in_book,
        summary="[[a4 비숍은 여전히 c6 나이트를 공격합니다.]] 압박을 유지하는 후퇴입니다.",
        why=["긴 게임을 고릅니다."],
        replies=[("Nf6", "메인."), ("d6", "모던 스타이니츠."), ("b5", "즉시 쫓기.")],
        alternatives=[("Bxc6", "익스체인지.")],
        traps=[TrapLine(title="노아의 방주", line_san=["d6", "d4"], text="퇴로가 막힙니다.")],
        mine=None,
        engine=None,
        sources=["book", "llm"],
        model="opus",
        created_at=datetime(2026, 9, 12),
    )


@pytest.fixture(autouse=True)
def _fake_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    log = tmp_path / "claude.log"
    settings = get_settings()
    monkeypatch.setattr(settings, "chat_claude_command", f"{sys.executable} {FAKE}")
    monkeypatch.setattr(settings, "chat_workdir", str(tmp_path / "work"))
    monkeypatch.setattr(settings, "chat_timeout_seconds", 20.0)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    chat_svc._sessions.clear()
    yield log
    chat_svc._sessions.clear()


def events(text: str) -> list[dict[str, Any]]:
    out = []
    for frame in text.split("\n\n"):
        data = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


# ---------- the prompt ----------


def test_the_prompt_carries_the_note_the_student_is_reading() -> None:
    opening = OpeningContext(
        fen_before=BEFORE,
        san="Ba4",
        note_summary="[[a4 비숍은 여전히 c6 나이트를 공격합니다.]] 압박을 유지합니다.",
        section="전형적인 실수와 함정",
        quote="[[c4 폰이 b3 비숍의 퇴로를 막습니다.]]",
    )
    prompt = chat_prompt.build_live_prompt(
        fen_after([*RUY, "Ba4"]),
        chess.STARTING_FEN,
        [*RUY, "Ba4"],
        "white",
        1500,
        None,
        "루이 로페즈: 모피 방어",
        opening,
    )
    assert "## 지금 보는 해설" in prompt
    assert "수: Ba4" in prompt and BEFORE in prompt
    assert "이 수가 이르는 국면 이름: 루이 로페즈: 모피 방어" in prompt
    assert "학생이 보고 있는 섹션: 전형적인 실수와 함정" in prompt
    # The verification marks are the panel's, not the model's.
    assert "[[" not in prompt.split("<facts>")[0]
    assert "a4 비숍은 여전히 c6 나이트를 공격합니다." in prompt
    assert '학생이 인용한 문장: "c4 폰이 b3 비숍의 퇴로를 막습니다."' in prompt
    # Everything the live chat already said is still there.
    assert prompt.startswith(chat_prompt.LIVE_INTRO)
    assert "<facts>" in prompt and "structure" in prompt


def test_a_question_without_a_note_looks_exactly_as_it_did() -> None:
    args = (fen_after(RUY), chess.STARTING_FEN, RUY, "white", 1500)
    assert "지금 보는 해설" not in chat_prompt.build_live_prompt(*args)


# ---------- one conversation per move ----------


async def test_the_conversation_is_keyed_by_the_move_not_the_position(
    aclient: AsyncClient,
) -> None:
    """Same FEN, another move: another conversation. That is the whole point of the key —
    the student is arguing about 4.Ba4, not about the position after 3...a6 (plan §10.3)."""
    body: dict[str, Any] = {
        "message": "왜 이 수인가요?",
        "fen": fen_after([*RUY, "Ba4"]),
        "moves_san": [*RUY, "Ba4"],
        "user_color": "white",
        "opening": {"fen_before": BEFORE, "san": "Ba4", "note_summary": "압박을 유지합니다."},
    }
    first = events((await aclient.post("/play/chat", json=body)).text)[0]
    session_id = first["session_id"]
    session = chat_svc.get_session(session_id)
    assert session is not None and session.fen == f"{BEFORE}|Ba4"
    assert "지금 보는 해설" in session.system_prompt

    # The same move again continues it, even from the board position the answer moved on to.
    again = events((await aclient.post("/play/chat", json={**body, "session_id": session_id})).text)
    assert again[0]["session_id"] == session_id and again[0]["resumed"] is True

    other = {
        **body,
        "session_id": session_id,
        "fen": fen_after([*RUY, "Bxc6"]),
        "moves_san": [*RUY, "Bxc6"],
        "opening": {"fen_before": BEFORE, "san": "Bxc6", "note_summary": "교환합니다."},
    }
    ev = events((await aclient.post("/play/chat", json=other)).text)[0]
    assert ev["session_id"] != session_id
    assert chat_svc.get_session(ev["session_id"]) is not None


async def test_a_live_game_question_is_still_keyed_by_the_position(aclient: AsyncClient) -> None:
    body = {"message": "계획은?", "fen": fen_after(RUY), "moves_san": RUY}
    ev = events((await aclient.post("/play/chat", json=body)).text)[0]
    session = chat_svc.get_session(ev["session_id"])
    assert session is not None and session.fen == fen_after(RUY)


# ---------- the question chips ----------


def test_suggested_questions_come_from_the_note_itself() -> None:
    questions = opening_notes.suggested_questions(note())
    assert questions == [
        "왜 노아의 방주인가요?",
        "Ba4 대신 Bxc6는 왜 안 되나요?",
        "상대가 Nf6로 응수하면 내 계획은?",
        "상대가 d6로 응수하면 내 계획은?",
    ]
    assert len(questions) <= opening_notes.SUGGESTED_QUESTIONS


def test_an_off_book_move_asks_about_the_book_first() -> None:
    questions = opening_notes.suggested_questions(note("Bc4", in_book=False))
    assert questions[0] == "이 수가 나쁘지 않다면 왜 책에 없나요?"
    assert "Bc4 대신 Bxc6는 왜 안 되나요?" in questions
    assert len(questions) == opening_notes.SUGGESTED_QUESTIONS


def test_a_bare_note_offers_nothing_to_ask() -> None:
    bare = note().model_copy(update={"traps": [], "alternatives": [], "replies": []})
    assert opening_notes.suggested_questions(bare) == []


# ---------- keeping an answer on the note ----------


def test_an_answer_the_student_keeps_lands_on_the_note(client: TestClient) -> None:
    """`client` (not `aclient`) so the app's lifespan seeds the 4.Ba4 note to append to."""
    body = {
        "fen": BEFORE,
        "san": "Ba4",
        "question": "왜 노아의 방주에서 비숍이 갇히나요?",
        "answer": "갈 칸이 하나도 안전하지 않다는 뜻입니다.",
        "boards": [
            {
                "fen": fen_after([*RUY, "Ba4", "d6"]),
                "start_fen": fen_after([*RUY, "Ba4"]),
                "moves": ["d6"],
                "last_move": ["d7", "d6"],
                "caption": "보드 1 · 4…d6 뒤",
                "highlights": ["b3"],
                "n": 1,
            }
        ],
        "unverified": ["a2", "c2"],
    }
    res = client.post("/openings/note/addendum", json=body)
    assert res.status_code == 200, res.text
    stored = res.json()
    assert len(stored["addenda"]) == 1
    kept = stored["addenda"][0]
    assert kept["question"] == body["question"] and kept["answer"] == body["answer"]
    assert kept["unverified"] == ["a2", "c2"]
    assert kept["boards"][0]["caption"] == "보드 1 · 4…d6 뒤"
    assert kept["boards"][0]["moves"] == ["d6"]
    assert kept["created_at"]

    # It comes back with the note, and the rest of the note is untouched.
    again = client.get("/openings/note", params={"fen": BEFORE, "san": "Ba4"}).json()
    assert again["addenda"] == stored["addenda"]
    assert again["summary"] == stored["summary"] and again["model"] == stored["model"]

    # A second answer is appended, not replaced.
    second = client.post("/openings/note/addendum", json={**body, "question": "그럼 c3는요?"})
    assert [a["question"] for a in second.json()["addenda"]] == [body["question"], "그럼 c3는요?"]


async def test_keeping_an_answer_needs_a_note_to_keep_it_on(aclient: AsyncClient) -> None:
    res = await aclient.post(
        "/openings/note/addendum",
        json={"fen": BEFORE, "san": "Nc3", "question": "q", "answer": "a"},
    )
    assert res.status_code == 404 and res.json()["detail"] == opening_notes.NO_NOTE
    res = await aclient.post(
        "/openings/note/addendum",
        json={"fen": BEFORE, "san": "Qxh8", "question": "q", "answer": "a"},
    )
    assert res.status_code == 422


def test_the_questions_come_back_with_the_note(client: TestClient) -> None:
    """The panel draws its chips from the note it already has, without another call."""
    stored = client.get("/openings/note", params={"fen": BEFORE, "san": "Ba4"}).json()
    assert stored["questions"], "the seeded 4.Ba4 note has traps, alternatives and replies"
    assert stored["questions"][0].startswith("왜 ")
    assert len(stored["questions"]) <= opening_notes.SUGGESTED_QUESTIONS


def test_a_trap_title_that_is_a_sentence_is_quoted_instead() -> None:
    """Real titles look like "공짜 폰은 없다 — 5.Nxe5?", which "왜 …인가요?" cannot swallow."""
    sentence = note().model_copy(
        update={
            "traps": [TrapLine(title="공짜 폰은 없다 — 5.Nxe5?", line_san=[], text="")],
            "alternatives": [],
            "replies": [],
        }
    )
    assert opening_notes.suggested_questions(sentence) == [
        '"공짜 폰은 없다 — 5.Nxe5?" 함정을 자세히 설명해 주세요.'
    ]
