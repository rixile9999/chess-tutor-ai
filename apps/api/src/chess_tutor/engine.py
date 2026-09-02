"""Stockfish access (layer 1 oracle). The engine runs as a separate UCI process.

`Engine` wraps one process. `pool` hands out engines to worker threads so a whole game can be
analysed with a single process instead of paying the start-up cost per position.
"""

from __future__ import annotations

import atexit
import os
import shutil
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import chess
import chess.engine

from chess_tutor.config import get_settings


@dataclass(frozen=True)
class Line:
    rank: int
    score_cp: int | None
    """Centipawns from White's point of view; None when a mate score is reported."""
    mate: int | None
    pv: list[chess.Move]


def find_stockfish() -> str | None:
    return (
        os.environ.get("STOCKFISH_PATH")
        or get_settings().stockfish_path
        or shutil.which("stockfish")
    )


def _name_from_uci_id(raw: str) -> str:
    """'Stockfish 18' -> 'stockfish-18'; anything unrecognised -> 'stockfish'."""
    tokens = raw.split()
    if len(tokens) >= 2 and tokens[0].lower() == "stockfish":
        return f"stockfish-{tokens[1].lower()}"
    return "stockfish"


class Engine:
    def __init__(
        self,
        path: str | None = None,
        threads: int | None = None,
        hash_mb: int | None = None,
    ) -> None:
        resolved = path or find_stockfish()
        if resolved is None:
            raise RuntimeError("Stockfish not found: set STOCKFISH_PATH or install it on PATH")
        self._engine = chess.engine.SimpleEngine.popen_uci(resolved)
        settings = get_settings()
        options = {
            "Threads": threads if threads is not None else settings.engine_threads,
            "Hash": hash_mb if hash_mb is not None else settings.engine_hash_mb,
        }
        supported = {k: v for k, v in options.items() if k in self._engine.options}
        if supported:
            self._engine.configure(supported)
        self.name = _name_from_uci_id(self._engine.id.get("name", ""))
        """Cache key for analyses, e.g. 'stockfish-18'."""

    def close(self) -> None:
        try:
            self._engine.quit()
        except chess.engine.EngineError:
            pass

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def analyse(
        self, board: chess.Board, depth: int = 18, multipv: int = 3, game: object | None = None
    ) -> list[Line]:
        """Top `multipv` lines at `depth`.

        `game` groups searches that may share the engine's transposition table (the positions
        of one game). Every new token, and every call without one, starts with `ucinewgame`, so
        a result depends only on the position, depth and multipv, never on what this process
        searched before: the same FEN gives the same lines whether it comes from a game
        analysis, an ad-hoc request, or a test."""
        infos = self._engine.analyse(
            board, chess.engine.Limit(depth=depth), multipv=multipv, game=game or object()
        )
        lines: list[Line] = []
        for info in infos:
            if "score" not in info:
                continue
            score = info["score"].white()
            lines.append(
                Line(
                    rank=int(info.get("multipv", 1)),
                    score_cp=score.score(),
                    mate=score.mate(),
                    pv=list(info.get("pv", [])),
                )
            )
        lines.sort(key=lambda line: line.rank)
        return lines

    def analyse_many(
        self, boards: Iterable[chess.Board], depth: int = 18, multipv: int = 3
    ) -> list[list[Line]]:
        """Analyse several positions with this one process, in order, sharing one table."""
        game = object()
        return [self.analyse(board, depth=depth, multipv=multipv, game=game) for board in boards]


class EnginePool:
    """A few long-lived engine processes shared by worker threads.

    `borrow()` gives a thread exclusive use of one engine; at most `size` engines exist at once
    and callers beyond that wait. An engine that raised is discarded rather than reused."""

    def __init__(self, size: int = 2) -> None:
        self._idle: list[Engine] = []
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(size)

    @contextmanager
    def borrow(self) -> Iterator[Engine]:
        self._slots.acquire()
        try:
            with self._lock:
                engine = self._idle.pop() if self._idle else None
            if engine is None:
                engine = Engine()
            try:
                yield engine
            except BaseException:
                engine.close()
                raise
            with self._lock:
                self._idle.append(engine)
        finally:
            self._slots.release()

    def close(self) -> None:
        with self._lock:
            engines, self._idle = self._idle, []
        for engine in engines:
            engine.close()


pool = EnginePool()

# python-chess runs each engine's event loop on a non-daemon thread, and the interpreter joins
# those threads before ordinary atexit handlers run. Idle engines must therefore be quit from
# the threading shutdown hook (the one concurrent.futures uses), or process exit blocks forever.
_register_thread_atexit = getattr(threading, "_register_atexit", None)
if _register_thread_atexit is not None:
    _register_thread_atexit(pool.close)
else:  # pragma: no cover - older interpreters
    atexit.register(pool.close)
