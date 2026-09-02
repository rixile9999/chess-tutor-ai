"""Tactical motif detectors (layer 2: concept extraction).

Every detector is deterministic and works on a position plus the move just played.
The output is data, not prose: the verbalization layer turns it into sentences and
the verifier checks those sentences against the board again.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from chess_tutor.values import value_at

VALUABLE = 3  # minor piece or better counts as a target


@dataclass(frozen=True)
class Motif:
    kind: str
    """"discovered_attack" | "fork"."""
    mover: chess.Square
    """Square the moving piece landed on."""
    attacker: chess.Square
    """Piece delivering the attack: the uncovered slider, or the forking piece itself."""
    targets: tuple[chess.Square, ...]
    with_check: bool
    safe: bool
    """True when the attacking piece is not attacked by the opponent after the move."""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "mover": chess.square_name(self.mover),
            "attacker": chess.square_name(self.attacker),
            "targets": [chess.square_name(t) for t in self.targets],
            "with_check": self.with_check,
            "safe": self.safe,
        }


def _valuable_targets(board: chess.Board, squares: chess.SquareSet) -> tuple[chess.Square, ...]:
    return tuple(sorted(sq for sq in squares if value_at(board, sq) >= VALUABLE))


def discovered_attacks(board: chess.Board, move: chess.Move) -> list[Motif]:
    """Sliders of the mover's colour that newly attack a valuable enemy piece through the
    square the moving piece vacated."""
    if not board.is_legal(move):
        raise ValueError(f"illegal move {move} in {board.fen()}")
    color = board.turn
    after = board.copy()
    after.push(move)
    enemy = after.occupied_co[not color]
    sliders = (
        after.pieces(chess.ROOK, color)
        | after.pieces(chess.BISHOP, color)
        | after.pieces(chess.QUEEN, color)
    )
    found: list[Motif] = []
    for sq in sliders:
        if sq == move.to_square:
            continue
        new_attacks = after.attacks(sq) & ~board.attacks(sq) & enemy
        through_vacated = chess.SquareSet(
            t for t in new_attacks if move.from_square in chess.SquareSet(chess.between(sq, t))
        )
        targets = _valuable_targets(after, through_vacated)
        if targets:
            found.append(
                Motif(
                    kind="discovered_attack",
                    mover=move.to_square,
                    attacker=sq,
                    targets=targets,
                    with_check=after.is_check(),
                    safe=not after.is_attacked_by(not color, sq),
                )
            )
    return found


def forks(board: chess.Board, move: chess.Move) -> list[Motif]:
    """The moved piece attacks two or more valuable enemy pieces (the king counts)."""
    if not board.is_legal(move):
        raise ValueError(f"illegal move {move} in {board.fen()}")
    color = board.turn
    after = board.copy()
    after.push(move)
    attacked = after.attacks(move.to_square) & after.occupied_co[not color]
    targets = _valuable_targets(after, attacked)
    if len(targets) < 2:
        return []
    return [
        Motif(
            kind="fork",
            mover=move.to_square,
            attacker=move.to_square,
            targets=targets,
            with_check=after.is_check(),
            safe=not after.is_attacked_by(not color, move.to_square),
        )
    ]


def detect(board: chess.Board, move: chess.Move) -> list[Motif]:
    return discovered_attacks(board, move) + forks(board, move)
