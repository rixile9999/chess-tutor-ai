#!/usr/bin/env python3
"""Stand-in for the `claude` CLI when an opening note is *streamed* (plan §10.2).

Reads the prompt from stdin like `claude -p` does, appends its arguments and that prompt to the
file named by FAKE_NOTE_LOG, and then prints the stream-json events a real run prints: an init
line, one tool call, the answer as text deltas, and a final result.

The answer is the same mixed note `fake_claude_note` writes, but as NDJSON — one JSON object
per section, in the order the prompt asks for. The deltas are cut at arbitrary offsets, never
at newlines, so the server really has to reassemble the lines before it can read a section.

When the command line says `--output-format json` (no `stream-json`), this falls through to
`fake_claude_note`: that is the one-shot path the 20-second fallback retries with, and the two
fakes must agree on what the note says.

FAKE_STREAM_MODE picks a variant:
  ok       the whole answer (default)
  prose    init, a paragraph that is not JSON, then a normal result (no section ever arrives)
  hang     init, then sleep FAKE_STREAM_HANG seconds (default 30) without writing a section
  partial  init and the summary line only, then exit 1: an answer that was cut off
  messy    the real CLI's two habits: a sentence in front of the first object, and a raw
           newline inside a string of the second one
  crash    init, then exit 1 with a message on stderr
FAKE_STREAM_CHUNK sets how many characters go into one text delta (default 37).
FAKE_STREAM_PAUSE waits that long between deltas (default 0.01s).
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_claude_note as one_shot  # noqa: E402

SESSION = "fake-note-stream"


def emit(obj: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def sections() -> list[dict[str, object]]:
    """The one-shot note, split into the five NDJSON lines the streamed prompt asks for.

    The claims travel with the section they belong to: the summary carries the one that holds
    and the one that does not, `why` carries none at all (so its marked sentence is demoted),
    and the trap carries the claim about the position its line reaches."""
    note = one_shot.note()
    claims = list(note["claims"])  # type: ignore[arg-type]
    return [
        {"section": "summary", "text": note["summary"], "claims": claims[:2]},
        {"section": "why", "paragraphs": note["why"], "claims": []},
        {"section": "replies", "items": note["replies"], "claims": []},
        {"section": "alternatives", "items": note["alternatives"], "claims": []},
        {"section": "traps", "items": note["traps"], "claims": claims[2:]},
    ]


def answer() -> str:
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in sections()) + "\n"


def stream(event: dict[str, object]) -> None:
    emit({"type": "stream_event", "event": event, "session_id": SESSION})


def deltas(text: str) -> None:
    """Text as the CLI sends it: chunks that pay no attention to line boundaries."""
    size = int(os.environ.get("FAKE_STREAM_CHUNK", "37"))
    pause = float(os.environ.get("FAKE_STREAM_PAUSE", "0.01"))
    stream({"type": "content_block_start", "index": 1, "content_block": {"type": "text"}})
    for start in range(0, len(text), size):
        stream(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": text[start : start + size]},
            }
        )
        time.sleep(pause)
    stream({"type": "content_block_stop", "index": 1})


def tool_call() -> None:
    emit(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_note_1",
                        "name": "mcp__chess__analyse",
                        "input": {"fen": one_shot.trap_fen(), "depth": 12},
                    }
                ],
            },
            "session_id": SESSION,
        }
    )


def result(ok: bool = True) -> None:
    emit(
        {
            "type": "result",
            "subtype": "success" if ok else "error",
            "is_error": not ok,
            "duration_ms": 1234,
            "num_turns": 2,
            "session_id": SESSION,
            "result": "(줄 단위로 이미 보냈습니다)",
        }
    )


def main() -> int:
    args = sys.argv[1:]
    if "stream-json" not in args:
        # The fallback retry: answer the one-shot way, with the same note. That fake reads
        # stdin and writes its own log line, so this one must not have touched either yet.
        return one_shot.main()
    prompt = sys.stdin.read()
    log = os.environ.get("FAKE_NOTE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt}, ensure_ascii=False) + "\n")

    mode = os.environ.get("FAKE_STREAM_MODE", "ok")
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": SESSION,
            "mcp_servers": [{"name": "chess", "status": "connected"}],
            "model": "fake",
        }
    )
    if mode == "crash":
        sys.stderr.write("fake claude: boom\n")
        return 1
    if mode == "hang":
        time.sleep(float(os.environ.get("FAKE_STREAM_HANG", "30")))
        return 0
    if mode == "prose":
        deltas("이 수는 압박을 유지하는 좋은 후퇴입니다. JSON 대신 산문으로 씁니다.\n")
        result()
        return 0
    tool_call()
    if mode == "messy":
        lines = [json.dumps(s, ensure_ascii=False) for s in sections()]
        lines[0] = "확인했습니다. 도구로 확인한 내용을 씁니다." + lines[0]
        lines[1] = lines[1].replace('", "', '",\n"', 1)
        deltas("\n".join(lines) + "\n")
        result()
        return 0
    if mode == "partial":
        deltas(json.dumps(sections()[0], ensure_ascii=False) + "\n")
        sys.stderr.write("fake claude: cut off\n")
        return 1
    deltas(answer())
    result()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
