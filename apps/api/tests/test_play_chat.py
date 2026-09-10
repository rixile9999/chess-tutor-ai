"""Live position chat for practice games (M7): a session without a stored game, keyed by
the position, with a system prompt made of the moves so far, the structure and the plans."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import chess
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db, models
from chess_tutor.config import get_settings
from chess_tutor.services import chat as chat_svc
from chess_tutor.services import chat_prompt

FAKE = Path(__file__).parent / "fixtures" / "fake_claude.py"

# Carlsbad tabiya (QGD Exchange) with Black to move.
CARLSBAD = ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "cxd5", "exd5", "Bg5", "Be7", "e3", "c6", "Bd3"]


def _fen_after(moves: list[str]) -> str:
    board = chess.Board()
    for san in moves:
        board.push_san(san)
    return board.fen()


@pytest.fixture(autouse=True)
def _fake_claude(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
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


def _events(text: str) -> list[dict[str, Any]]:
    out = []
    for frame in text.split("\n\n"):
        data = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def test_live_prompt_lists_moves_structure_and_plans() -> None:
    fen = _fen_after(CARLSBAD)
    prompt = chat_prompt.build_live_prompt(
        fen, chess.STARTING_FEN, CARLSBAD, "black", 1500, "Maia 1500", "QGD Exchange"
    )
    assert prompt.startswith(chat_prompt.LIVE_INTRO)
    assert "<facts>" in prompt and fen in prompt
    facts = json.loads(prompt.split("<facts>\n")[1].split("\n</facts>")[0])
    assert facts["game"] == {
        "student_color": "black",
        "opponent": "Maia 1500",
        "opening": "QGD Exchange",
        "in_progress": True,
    }
    assert facts["position"]["side_to_move"] == "black" and facts["position"]["move_number"] == 7
    assert facts["moves_so_far"].startswith("1. d4") and "7. Bd3" in facts["moves_so_far"]
    assert facts["structure"]["key"] == "carlsbad"
    assert facts["plans"]["white"] and facts["plans"]["black"]
    # No engine numbers in a live prompt: the tutor asks its tools.
    assert "eval_before" not in facts and "cp" not in json.dumps(facts["position"])


def test_live_prompt_keeps_only_the_last_moves() -> None:
    moves = ["Nf3", "Nf6", "Ng1", "Ng8"] * 10
    prompt = chat_prompt.build_live_prompt(_fen_after(moves), chess.STARTING_FEN, moves, None, 1500)
    facts = json.loads(prompt.split("<facts>\n")[1].split("\n</facts>")[0])
    labels = facts["moves_so_far"].split()
    assert len(labels) <= 2 * chat_prompt.LIVE_MOVES_CONTEXT + 1
    assert facts["game"]["student_color"] is None


def test_live_prompt_tolerates_a_bad_move_list() -> None:
    prompt = chat_prompt.build_live_prompt(
        chess.STARTING_FEN, chess.STARTING_FEN, ["Zz9"], "white", 1500
    )
    assert "<facts>" in prompt


async def test_live_chat_endpoint_follows_the_position(
    aclient: AsyncClient, _fake_claude: Path
) -> None:
    fen = _fen_after(CARLSBAD)
    body = {
        "message": "지금 흑의 계획은 뭔가요?",
        "fen": fen,
        "moves_san": CARLSBAD,
        "user_color": "black",
        "opponent": "Maia 1500",
    }
    r = await aclient.post("/play/chat", json=body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0]["type"] == "session" and events[-1]["type"] == "done"
    session_id = events[0]["session_id"]
    session = chat_svc.get_session(session_id)
    assert session is not None and session.game_id is None and session.fen == fen
    assert session.ply == len(CARLSBAD)
    assert "carlsbad" in session.system_prompt
    # Squares occupied in the position are grounded from the start.
    assert "d3" in session.known_squares

    # Same position: the conversation continues.
    r = await aclient.post("/play/chat", json={**body, "session_id": session_id})
    assert _events(r.text)[0]["session_id"] == session_id

    # After a move the position changed: a fresh session, even with the old id.
    next_moves = [*CARLSBAD, "O-O"]
    r = await aclient.post(
        "/play/chat",
        json={
            **body,
            "session_id": session_id,
            "fen": _fen_after(next_moves),
            "moves_san": next_moves,
        },
    )
    ev = _events(r.text)[0]
    assert ev["session_id"] != session_id and ev["resumed"] is False

    async with db.session_factory()() as s:
        rows = list(await s.scalars(select(models.ChatTurn)))
    assert rows and all(row.game_id is None for row in rows)
    assert {row.role for row in rows} == {"user", "assistant"}


async def test_live_chat_rejects_bad_positions(aclient: AsyncClient) -> None:
    r = await aclient.post("/play/chat", json={"message": "?", "fen": "not a fen"})
    assert r.status_code == 422
    # Two white kings: parses but is not a legal position.
    r = await aclient.post(
        "/play/chat", json={"message": "?", "fen": "k7/8/8/8/8/8/8/KK6 w - - 0 1"}
    )
    assert r.status_code == 422


async def test_live_session_id_is_not_reused_for_a_stored_game(
    aclient: AsyncClient, _fake_claude: Path
) -> None:
    """A live session id handed to the review chat must not hijack that conversation."""
    r = await aclient.post(
        "/play/chat", json={"message": "?", "fen": chess.STARTING_FEN, "moves_san": []}
    )
    live_id = _events(r.text)[0]["session_id"]
    async with db.session_factory()() as s:
        game = models.Game(
            source="pgn",
            source_id="live-vs-stored",
            pgn='[Event "x"]\n[Result "*"]\n\n1. e4 e5 *',
            white="a",
            black="b",
            result="*",
        )
        s.add(game)
        await s.commit()
        game_id = game.id
    r = await aclient.post(
        f"/review/{game_id}/1/chat?depth=6", json={"message": "?", "session_id": live_id}
    )
    if r.status_code == 200:
        assert _events(r.text)[0]["session_id"] != live_id
    else:
        # No engine on this machine: the review chat cannot build its facts at all.
        assert r.status_code in (500, 503)
