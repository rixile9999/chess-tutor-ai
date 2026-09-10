#!/usr/bin/env python3
"""Stand-in for the `claude` CLI when an opening note is written.

Reads the prompt from stdin like `claude -p` does, appends its arguments and that prompt to the
file named by FAKE_NOTE_LOG (one JSON line per run), and prints the single JSON envelope
`--output-format json` produces, whose `result` is the note the model wrote.

The note is deliberately mixed, so the server's checks all have something to do:
  * one [[…]] sentence with claims that hold and one with a claim that does not (demoted),
  * one [[…]] sentence with no claims at all (demoted),
  * one trap line that plays and one that does not (dropped),
  * `mine` and `engine` filled in with nonsense, which the server must overwrite.

FAKE_NOTE_MODE picks a variant: ok (default), fenced (the JSON inside a code fence),
prose (no JSON at all), crash (exit 1 with a message on stderr), error (an is_error envelope).
FAKE_NOTE_SUMMARY overrides the summary, so a regeneration can be told from the first answer.
"""

from __future__ import annotations

import json
import os
import sys

import chess

GOOD = "[[c4 비숍이 f7을 공격합니다.]]"
WRONG = "[[a1 룩이 h8을 공격합니다.]]"
UNBACKED = "[[e4 폰은 이 국면의 열쇠입니다.]]"
TRAP = "[[f3 나이트가 e5 폰을 공격합니다.]]"
TRAP_LINE = ["Nf6", "O-O"]
MOVES = "e4 e5 Nf3 Nc6 Bb5 a6 Bc4"
"""The move this fixture is written for; the trap's claim names the position it reaches."""


def trap_fen() -> str:
    board = chess.Board()
    for san in [*MOVES.split(), *TRAP_LINE]:
        board.push_san(san)
    return board.fen()


def note() -> dict[str, object]:
    summary = os.environ.get("FAKE_NOTE_SUMMARY") or f"{GOOD} {WRONG} 압박을 유지합니다."
    return {
        "summary": summary,
        "why": [f"{UNBACKED} 중앙을 먼저 정리하는 편이 낫습니다."],
        "replies": [["Nf6", "e4를 맞공격합니다."], ["b5", "비숍을 다시 쫓습니다."]],
        "alternatives": [["Ba4", "책의 메인. 압박을 유지합니다."]],
        "traps": [
            {
                "title": "되잡을 수 없는 폰",
                "line_san": TRAP_LINE,
                "text": f"{TRAP} 그래서 폰을 딸 수 없습니다.",
            },
            {"title": "재생되지 않는 수순", "line_san": ["Qxh8"], "text": "버려져야 합니다."},
        ],
        "mine": "이 줄은 서버가 덮어씁니다.",
        "engine": "이 줄도 서버가 덮어씁니다.",
        "claims": [
            {"text": GOOD.strip("[]"), "kind": "attacks", "subject": "c4", "object": "f7"},
            {"text": WRONG.strip("[]"), "kind": "attacks", "subject": "a1", "object": "h8"},
            {
                "text": TRAP.strip("[]"),
                "kind": "attacks",
                "subject": "f3",
                "object": "e5",
                "fen": trap_fen(),
            },
        ],
    }


def main() -> int:
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    log = os.environ.get("FAKE_NOTE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt}, ensure_ascii=False) + "\n")
    mode = os.environ.get("FAKE_NOTE_MODE", "ok")
    if mode == "crash":
        sys.stderr.write("Error: could not reach the model.\n")
        return 1
    body = json.dumps(note(), ensure_ascii=False)
    if mode == "fenced":
        body = f"여기 있습니다.\n```json\n{body}\n```\n"
    elif mode == "prose":
        body = "JSON을 쓰지 못했습니다."
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": mode == "error",
        "result": body,
        "num_turns": 2,
    }
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
