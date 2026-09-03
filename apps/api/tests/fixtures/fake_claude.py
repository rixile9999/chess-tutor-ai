#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests: replays a recorded stream-json answer.

Reads the question from stdin like `claude -p` does, echoes its arguments and the question to
the file named by FAKE_CLAUDE_LOG (one JSON line per run), and prints the event sequence a
real answer produces: partial text, a show_board tool call and its result, more text, a rate
limit event and the final result. FAKE_CLAUDE_MODE=crash exits early with an error;
FAKE_CLAUDE_PAUSE (seconds) waits between the two halves so a test can push a board event in
the middle of the stream."""

from __future__ import annotations

import json
import os
import sys
import time


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    if "--session-id" in args:
        sid = args[args.index("--session-id") + 1]
    elif "--resume" in args:
        sid = args[args.index("--resume") + 1]
    else:
        sid = "no-session"
    log = os.environ.get("FAKE_CLAUDE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"args": args, "prompt": prompt}, ensure_ascii=False) + "\n")
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    pause = float(os.environ.get("FAKE_CLAUDE_PAUSE", "0.05"))
    mcp_status = "connected" if mode != "no-mcp" else "failed"

    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": sid,
            "tools": ["mcp__chess__show_board"],
            "mcp_servers": [{"name": "chess", "status": mcp_status}],
            "model": "fake",
        }
    )
    if mode == "crash":
        sys.stderr.write("fake claude: boom\n")
        return 1

    def stream(event: dict) -> None:
        emit({"type": "stream_event", "event": event, "session_id": sid})

    # First text block, streamed as deltas.
    stream(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    )
    for piece in ("Nf5는 ", "블런더입니다. ", "d5 나이트가 f6를 겨냥합니다."):
        stream(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": piece},
            }
        )
    emit(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Nf5는 블런더입니다. d5 나이트가 f6를 겨냥합니다."}
                ],
            },
            "session_id": sid,
        }
    )
    stream({"type": "content_block_stop", "index": 0})

    # A tool call.
    tool_id = "toolu_fake_1"
    tool_input = {
        "fen": "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 5 21",
        "moves": ["Nxf6+"],
        "caption": "나이트가 f6를 잡으며 체크",
    }
    stream(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": tool_id,
                "name": "mcp__chess__show_board",
                "input": {},
            },
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "mcp__chess__show_board",
                        "input": tool_input,
                    }
                ],
            },
            "session_id": sid,
        }
    )
    stream({"type": "content_block_stop", "index": 1})
    time.sleep(pause)
    emit(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "shown": True,
                                        "fen": "x",
                                        "moves": ["Nxf6+"],
                                        "last_move": ["d5", "f6"],
                                        "note": "표시했습니다.",
                                    }
                                ),
                            }
                        ],
                        "is_error": False,
                    }
                ],
            },
            "session_id": sid,
        }
    )

    # Second text block: mentions f6 (grounded by the tool result) and h3 (grounded by nothing).
    stream(
        {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}}
    )
    stream(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "text_delta", "text": "f6를 잡은 뒤 h3 폰은 그대로입니다."},
        }
    )
    emit(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "f6를 잡은 뒤 h3 폰은 그대로입니다."}],
            },
            "session_id": sid,
        }
    )
    stream({"type": "content_block_stop", "index": 2})

    emit(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "unifiedWindows": {
                    "five_hour": {"utilization": 0.1, "resetsAt": 1},
                    "seven_day": {"utilization": 0.36, "resetsAt": 2},
                },
            },
            "session_id": sid,
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success" if mode != "max-turns" else "error_max_turns",
            "is_error": False,
            "duration_ms": 12,
            "num_turns": 2,
            "total_cost_usd": 0.0,
            "session_id": sid,
            "result": "Nf5는 블런더입니다.",
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
