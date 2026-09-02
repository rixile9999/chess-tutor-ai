"""Stockfish access (layer 1 oracle). The engine runs as a separate UCI process.

`Engine` wraps one process. `pool` hands out engines to worker threads so a whole game can be
analysed with a single process instead of paying the start-up cost per position.
"""

from __future__ import annotations

import atexit
import os
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import chess
import chess.engine

from chess_tutor.config import get_settings

BUSY = "엔진이 모두 사용 중입니다. 잠시 후 다시 시도해 주세요."


class EngineBusy(RuntimeError):
    """Every pooled engine is checked out. Raised instead of waiting forever so a request can
    answer 503 rather than hang behind a whole-game analysis."""

    def __init__(self, message: str = BUSY) -> None:
        super().__init__(message)


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

    def analyse(self, board: chess.Board, depth: int = 18, multipv: int = 3) -> list[Line]:
        """Top `multipv` lines at `depth`.

        Every search gets a fresh `game` token, so python-chess sends `ucinewgame` and the
        engine clears its transposition table first. A result therefore depends only on the
        position, depth, multipv and the engine's options, never on what this process searched
        before: the same FEN gives the same lines whether it comes from a game analysis, an
        ad-hoc request, or a test, and a cached line is safe to reuse for either."""
        infos = self._engine.analyse(
            board, chess.engine.Limit(depth=depth), multipv=multipv, game=object()
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


class EnginePool:
    """A few long-lived engine processes shared by worker threads.

    `borrow()` gives a thread exclusive use of one engine; at most `size` engines exist at once.
    A caller beyond that waits at most `wait` seconds and is then told the pool is busy
    (`EngineBusy`) instead of blocking behind a whole-game analysis for minutes. An engine that
    raised is discarded rather than reused."""

    def __init__(self, size: int | None = None, wait: float | None = None) -> None:
        settings = get_settings()
        self.size = settings.engine_pool_size if size is None else size
        self.wait = settings.engine_wait_seconds if wait is None else wait
        self._idle: list[Engine] = []
        self._busy: set[Engine] = set()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self.size)

    @contextmanager
    def borrow(self, wait: float | None = None) -> Iterator[Engine]:
        if not self._slots.acquire(timeout=self.wait if wait is None else wait):
            raise EngineBusy
        try:
            with self._lock:
                engine = self._idle.pop() if self._idle else None
            if engine is None:
                engine = Engine()
            with self._lock:
                self._busy.add(engine)
            try:
                yield engine
            except BaseException:
                with self._lock:
                    self._busy.discard(engine)
                engine.close()
                raise
            with self._lock:
                self._busy.discard(engine)
                self._idle.append(engine)
        finally:
            self._slots.release()

    def close(self) -> None:
        """Quit every engine, including one that is checked out: at shutdown an engine left in a
        worker thread would otherwise keep its process alive."""
        with self._lock:
            engines = [*self._idle, *self._busy]
            self._idle, self._busy = [], set()
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
