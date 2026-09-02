"""Pawn-structure classifier (layer 2: concept extraction).

Deterministic rules on pawn placement only. Each rule is written from White's point of
view and also tried on the mirrored board, so a structure that Black "owns" (an IQP on
d5, a reversed Stonewall) is found by the same rule with the side flipped. Rules are
ordered from the most specific to the most generic; the first match wins.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import chess

from chess_tutor import schemas

Side = Literal["white", "black", "both"]

STRUCTURE_NAMES: dict[str, str] = {
    "hedgehog": "헤지호그",
    "maroczy": "마로치 바인드",
    "iqp": "고립 d폰",
    "hanging_pawns": "행잉 폰",
    "carlsbad": "칼스바드",
    "stonewall": "스톤월",
    "french_chain": "프렌치 사슬",
    "kid": "킹스 인디언",
    "benoni": "베노니",
    "boleslavsky_hole": "볼레슬랍스키 홀",
    "scheveningen": "셰베닝겐",
    "slav_caro": "슬라브/카로칸",
    "symmetrical_d": "대칭 d폰",
    "closed_center": "닫힌 센터",
    "open_center": "오픈 센터",
    "unclassified": "미분류",
}

MIN_SPAN_PLIES = 4

MIN_STRUCTURE_PAWNS = 6
"""Total pawns needed before a named structure is claimed."""

GENERIC_KEYS = frozenset({"closed_center", "open_center"})
"""Rules that only describe the central files; they still hold once the pieces are gone."""


@dataclass(frozen=True)
class Match:
    confidence: float
    defining: tuple[chess.Square, ...]
    side: Side


@dataclass(frozen=True)
class Pawns:
    """Pawn placement seen from White's point of view."""

    white: chess.SquareSet
    black: chess.SquareSet

    def w(self, name: str) -> bool:
        return chess.parse_square(name) in self.white

    def b(self, name: str) -> bool:
        return chess.parse_square(name) in self.black

    def w_file(self, file: str) -> chess.SquareSet:
        return self.white & chess.BB_FILES[FILES.index(file)]

    def b_file(self, file: str) -> chess.SquareSet:
        return self.black & chess.BB_FILES[FILES.index(file)]

    def w_any(self, *names: str) -> str | None:
        return next((n for n in names if self.w(n)), None)

    def b_any(self, *names: str) -> str | None:
        return next((n for n in names if self.b(n)), None)


FILES = "abcdefgh"
Rule = Callable[[Pawns], Match | None]


def _squares(*names: str | None) -> tuple[chess.Square, ...]:
    return tuple(chess.parse_square(n) for n in names if n)


def _blocked(pawns: Pawns, file: str) -> tuple[chess.Square, chess.Square] | None:
    """A white pawn with a black pawn directly in front of it on `file`."""
    for sq in pawns.w_file(file):
        if chess.square_rank(sq) < 7 and sq + 8 in pawns.black:
            return sq, sq + 8
    return None


# ---------- rules (white's point of view) ----------


def _hedgehog(p: Pawns) -> Match | None:
    a_pawn = p.b_any("a6", "a7")
    if not (a_pawn and p.b("b6") and p.b("d6") and p.b("e6")):
        return None
    if p.b_file("c") or not p.w("c4") or p.w_file("d"):
        return None
    confidence = 0.85
    if p.w("e4"):
        confidence += 0.05
    if a_pawn == "a6":
        confidence += 0.02
    e4 = "e4" if p.w("e4") else None
    return Match(confidence, _squares(a_pawn, "b6", "d6", "e6", "c4", e4), "black")


def _maroczy(p: Pawns) -> Match | None:
    if not (p.w("c4") and p.w("e4")) or p.w_file("d") or p.b_file("c"):
        return None
    d_pawn = p.b_any("d6", "d7")
    if d_pawn is None:
        return None
    confidence = 0.85
    if d_pawn == "d6":
        confidence += 0.05
    if p.b("g6"):
        confidence += 0.05
    return Match(confidence, _squares("c4", "e4", d_pawn), "white")


def _iqp(p: Pawns) -> Match | None:
    if not p.w("d4") or len(p.w_file("d")) != 1 or p.w_file("c") or p.w_file("e"):
        return None
    if p.b_file("d"):
        return None
    confidence = 0.85
    if p.b("e6"):
        confidence += 0.05
    if p.b_file("c"):
        confidence += 0.03
    return Match(confidence, _squares("d4"), "white")


def _hanging_pawns(p: Pawns) -> Match | None:
    if not (p.w("c4") and p.w("d4")) or p.w_file("b") or p.w_file("e"):
        return None
    if p.b_file("c") or p.b_file("d"):
        return None
    return Match(0.9, _squares("c4", "d4"), "white")


def _carlsbad(p: Pawns) -> Match | None:
    e_pawn = p.w_any("e3", "e2")
    c_pawn = p.b_any("c6", "c7")
    if not (p.w("d4") and e_pawn and p.b("d5") and c_pawn):
        return None
    if p.w_file("c") or p.b_file("e"):
        return None
    confidence = 0.85
    if c_pawn == "c6":
        confidence += 0.05
    if e_pawn == "e3":
        confidence += 0.03
    return Match(confidence, _squares("d4", e_pawn, "d5", c_pawn), "white")


def _stonewall(p: Pawns) -> Match | None:
    if not (p.w("d4") and p.w("e3") and p.w("f4")):
        return None
    confidence = 0.85
    c3 = "c3" if p.w("c3") else None
    if c3:
        confidence += 0.05
    if p.b("d5"):
        confidence += 0.05
    return Match(confidence, _squares(c3, "d4", "e3", "f4"), "white")


def _french_chain(p: Pawns) -> Match | None:
    if not (p.w("d4") and p.w("e5") and p.b("d5") and p.b("e6")):
        return None
    confidence = 0.85
    c3 = "c3" if p.w("c3") else None
    c5 = "c5" if p.b("c5") else None
    if c3:
        confidence += 0.05
    if c5:
        confidence += 0.05
    return Match(confidence, _squares(c3, "d4", "e5", c5, "d5", "e6"), "both")


def _kid(p: Pawns) -> Match | None:
    if not (p.w("d5") and p.w("e4") and p.b("d6") and p.b("e5")):
        return None
    confidence = 0.8
    c4 = "c4" if p.w("c4") else None
    if c4:
        confidence += 0.05
    if p.b("g6"):
        confidence += 0.05
    return Match(confidence, _squares(c4, "d5", "e4", "d6", "e5"), "both")


def _benoni(p: Pawns) -> Match | None:
    if not (p.w("d5") and p.b("c5") and p.b("d6")) or p.b_file("e"):
        return None
    confidence = 0.8
    e4 = "e4" if p.w("e4") else None
    c4 = "c4" if p.w("c4") else None
    if e4:
        confidence += 0.05
    if c4:
        confidence += 0.05
    return Match(confidence, _squares(c4, "d5", e4, "c5", "d6"), "black")


def _boleslavsky_hole(p: Pawns) -> Match | None:
    if not (p.b("d6") and p.b("e5") and p.w("e4")) or p.b_file("c") or p.w_file("d"):
        return None
    return Match(0.9, _squares("e4", "d6", "e5"), "black")


def _scheveningen(p: Pawns) -> Match | None:
    if not (p.b("d6") and p.b("e6") and p.w("e4")) or p.b_file("c") or p.w_file("d"):
        return None
    if p.w("c4"):
        return None
    confidence = 0.85
    a6 = "a6" if p.b("a6") else None
    if a6:
        confidence += 0.05
    return Match(confidence, _squares("e4", a6, "d6", "e6"), "black")


def _slav_caro(p: Pawns) -> Match | None:
    if p.b("c6") and p.b("e6") and not p.b_file("d") and p.w("d4") and not p.w_file("e"):
        return Match(0.9, _squares("d4", "c6", "e6"), "black")
    e_pawn = p.b_any("e6", "e7")
    if p.b("c6") and p.b("d5") and e_pawn and p.w("d4") and (p.w("c4") or p.w("e4")):
        wing = "c4" if p.w("c4") else "e4"
        return Match(0.7, _squares(wing, "d4", "c6", "d5", e_pawn), "black")
    return None


def _symmetrical_d(p: Pawns) -> Match | None:
    if not (p.w("d4") and p.b("d5")):
        return None
    if not p.w_file("e") and not p.b_file("e"):
        return Match(0.9, _squares("d4", "d5"), "both")
    if not p.w_file("c") and not p.b_file("c"):
        return Match(0.85, _squares("d4", "d5"), "both")
    return None


def _closed_center(p: Pawns) -> Match | None:
    d = _blocked(p, "d")
    e = _blocked(p, "e")
    if d and e:
        return Match(0.9, (*d, *e), "both")
    for blocked, other in ((d, "e"), (e, "d")):
        if blocked and p.w_file(other) and p.b_file(other):
            return Match(0.7, blocked, "both")
    return None


def _open_center(p: Pawns) -> Match | None:
    d_empty = not p.w_file("d") and not p.b_file("d")
    e_empty = not p.w_file("e") and not p.b_file("e")
    if d_empty and e_empty:
        return Match(0.95, (), "both")
    if d_empty or e_empty:
        other = "e" if d_empty else "d"
        remaining = p.w_file(other) | p.b_file(other)
        if _blocked(p, other):
            return None
        confidence = 0.9 if len(remaining) <= 1 else 0.8
        return Match(confidence, tuple(sorted(remaining)), "both")
    return None


RULES: tuple[tuple[str, Rule], ...] = (
    ("hedgehog", _hedgehog),
    ("maroczy", _maroczy),
    ("iqp", _iqp),
    ("hanging_pawns", _hanging_pawns),
    ("carlsbad", _carlsbad),
    ("stonewall", _stonewall),
    ("french_chain", _french_chain),
    ("kid", _kid),
    ("benoni", _benoni),
    ("boleslavsky_hole", _boleslavsky_hole),
    ("scheveningen", _scheveningen),
    ("slav_caro", _slav_caro),
    ("symmetrical_d", _symmetrical_d),
    ("closed_center", _closed_center),
    ("open_center", _open_center),
)


# ---------- public API ----------


def _flip_side(side: Side) -> Side:
    if side == "both":
        return side
    return "black" if side == "white" else "white"


def _mirror(match: Match) -> Match:
    return Match(
        match.confidence,
        tuple(chess.square_mirror(sq) for sq in match.defining),
        _flip_side(match.side),
    )


def _info(key: str, match: Match) -> schemas.StructureInfo:
    return schemas.StructureInfo(
        key=key,
        name=STRUCTURE_NAMES[key],
        confidence=round(min(1.0, max(0.0, match.confidence)), 2),
        defining_pawns=[chess.square_name(sq) for sq in match.defining],
        side=match.side,
    )


def _has_middlegame_material(board: chess.Board) -> bool:
    """Whether a named structure can be claimed at all. The named rules describe middlegames
    and the plans attached to them talk about knights, bishops and rooks, so both sides need
    a piece besides the king and the board needs enough pawns for the placement to be a
    structure rather than an accident of an endgame."""
    if chess.popcount(board.pawns) < MIN_STRUCTURE_PAWNS:
        return False
    pieces = board.occupied & ~board.pawns & ~board.kings
    return all(bool(pieces & board.occupied_co[color]) for color in chess.COLORS)


def classify(board: chess.Board) -> schemas.StructureInfo:
    pawns = Pawns(board.pieces(chess.PAWN, chess.WHITE), board.pieces(chess.PAWN, chess.BLACK))
    mirrored = Pawns(
        chess.SquareSet(chess.flip_vertical(int(pawns.black))),
        chess.SquareSet(chess.flip_vertical(int(pawns.white))),
    )
    middlegame = _has_middlegame_material(board)
    for key, rule in RULES:
        if not middlegame and key not in GENERIC_KEYS:
            continue
        match = rule(pawns)
        if match is not None:
            return _info(key, match)
        match = rule(mirrored)
        if match is not None:
            return _info(key, _mirror(match))
    return schemas.StructureInfo(
        key="unclassified", name=STRUCTURE_NAMES["unclassified"], confidence=1.0, side=None
    )


def key_at(board: chess.Board) -> str:
    return classify(board).key


def timeline(boards: Sequence[chess.Board]) -> list[schemas.StructureSpan]:
    """Structure spans over a game. `boards[i]` is the position after ply i (index 0 is the
    start). Consecutive plies with the same key merge; runs shorter than MIN_SPAN_PLIES are
    folded into the longer neighbour so the timeline shows phases, not flicker."""
    keys = [key_at(b) for b in boards]
    if not keys:
        return []
    runs: list[list[int | str]] = []  # [key, from_ply, to_ply]
    for ply, key in enumerate(keys):
        if runs and runs[-1][0] == key:
            runs[-1][2] = ply
        else:
            runs.append([key, ply, ply])

    def length(run: list[int | str]) -> int:
        return int(run[2]) - int(run[1]) + 1

    while len(runs) > 1:
        short = next((i for i, run in enumerate(runs) if length(run) < MIN_SPAN_PLIES), None)
        if short is None:
            break
        prev = runs[short - 1] if short > 0 else None
        nxt = runs[short + 1] if short + 1 < len(runs) else None
        if prev is not None and (nxt is None or length(prev) >= length(nxt)):
            prev[2] = runs[short][2]
        else:
            assert nxt is not None
            nxt[1] = runs[short][1]
        del runs[short]
        merged: list[list[int | str]] = []
        for run in runs:
            if merged and merged[-1][0] == run[0]:
                merged[-1][2] = run[2]
            else:
                merged.append(run)
        runs = merged

    return [
        schemas.StructureSpan(
            key=str(run[0]),
            name=STRUCTURE_NAMES[str(run[0])],
            from_ply=int(run[1]),
            to_ply=int(run[2]),
        )
        for run in runs
    ]
