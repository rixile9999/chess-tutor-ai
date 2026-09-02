"""Stockfish access (layer 1 oracle). The engine runs as a separate UCI process."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

import chess
import chess.engine


@dataclass(frozen=True)
class Line:
    rank: int
    score_cp: int | None
    """Centipawns from White's point of view; None when a mate score is reported."""
    mate: int | None
    pv: list[chess.Move]


def find_stockfish() -> str | None:
    return os.environ.get("STOCKFISH_PATH") or shutil.which("stockfish")


class Engine:
    def __init__(self, path: str | None = None) -> None:
        resolved = path or find_stockfish()
        if resolved is None:
            raise RuntimeError("Stockfish not found: set STOCKFISH_PATH or install it on PATH")
        self._engine = chess.engine.SimpleEngine.popen_uci(resolved)

    def close(self) -> None:
        self._engine.quit()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def analyse(self, board: chess.Board, depth: int = 18, multipv: int = 3) -> list[Line]:
        infos = self._engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        lines: list[Line] = []
        for info in infos:
            score = info["score"].white()
            lines.append(
                Line(
                    rank=int(info.get("multipv", 1)),
                    score_cp=score.score(),
                    mate=score.mate(),
                    pv=list(info.get("pv", [])),
                )
            )
        return lines
