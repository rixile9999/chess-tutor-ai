"""Tactical motif detectors (layer 2: concept extraction).

Every detector is deterministic and works on a position plus the move just played.
The output is data, not prose: the verbalization layer turns it into sentences and
the verifier checks those sentences against the board again. The Korean phrases
built by describe() are assembled only from the squares and piece letters stored in
the motif, so each one is traceable to the facts it came from.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import chess

from chess_tutor.values import PIECE_VALUE, value_at

VALUABLE = 3  # minor piece or better counts as a target

KOREAN_NAMES: dict[str, str] = {
    "discovered_attack": "디스커버드 어택",
    "fork": "포크",
    "pin": "핀",
    "skewer": "스큐어",
    "hanging_piece": "무방비 기물",
    "remove_defender": "수비수 제거",
    "overload": "과부하",
    "back_rank": "백랭크",
    "trapped_piece": "기물 트랩",
    "mate_threat": "메이트 위협",
}

UNDERDEFENDED_NAME = "수비 부족 기물"
"""Label of a hanging_piece motif whose target does have a defender: it is not defenceless,
it is only attacked by something cheaper than itself, so it must not be called 무방비."""

PIECE_KOREAN: dict[str, str] = {
    "K": "킹",
    "Q": "퀸",
    "R": "룩",
    "B": "비숍",
    "N": "나이트",
    "P": "폰",
}

_ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))
_DIAGONAL = ((1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass(frozen=True)
class Motif:
    kind: str
    """One of the KOREAN_NAMES keys."""
    mover: chess.Square
    """Square the moving piece landed on."""
    attacker: chess.Square
    """Piece delivering the attack: the uncovered slider, the forking piece itself, ..."""
    targets: tuple[chess.Square, ...]
    """Squares the motif is about. Order is kind-specific: pin/skewer [front, behind],
    remove_defender [defender, protected piece], overload [defender, attacked, other]."""
    with_check: bool
    safe: bool
    """True when the attacking piece is not attacked by the opponent after the move."""
    mover_piece: str = ""
    """Upper-case letter of the piece that moved (after promotion)."""
    attacker_piece: str = ""
    target_pieces: tuple[str, ...] = ()
    """Upper-case letters of the pieces on `targets`, taken from the position after the move
    or, for a piece the move captured, from the position before it."""
    line: tuple[str, ...] = ()
    """SAN moves from the position after the move that show the threat (e.g. the mating move)."""
    defended: bool = False
    """hanging_piece only: the target has a defender and is merely attacked by a cheaper
    piece, so it is labelled 수비 부족 기물 instead of 무방비 기물."""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "mover": chess.square_name(self.mover),
            "attacker": chess.square_name(self.attacker),
            "targets": [chess.square_name(t) for t in self.targets],
            "with_check": self.with_check,
            "safe": self.safe,
            "line": list(self.line),
            "label": label_of(self),
            "description": describe(self),
        }


# ---------- shared helpers ----------


def _require_legal(board: chess.Board, move: chess.Move) -> None:
    if not board.is_legal(move):
        raise ValueError(f"illegal move {move} in {board.fen()}")


def _after(board: chess.Board, move: chess.Move) -> chess.Board:
    after = board.copy(stack=False)
    after.push(move)
    return after


def _symbol(after: chess.Board, before: chess.Board, square: chess.Square) -> str:
    piece = after.piece_at(square) or before.piece_at(square)
    return piece.symbol().upper() if piece else ""


def _enemy_symbol(
    after: chess.Board, before: chess.Board, square: chess.Square, enemy: chess.Color
) -> str:
    """Letter of the enemy piece on `square`, read from the position after the move or, when
    the move captured it, from the position before."""
    for board in (after, before):
        piece = board.piece_at(square)
        if piece is not None and piece.color == enemy:
            return piece.symbol().upper()
    return ""


def _make(
    kind: str,
    before: chess.Board,
    after: chess.Board,
    *,
    mover: chess.Square,
    attacker: chess.Square,
    targets: Iterable[chess.Square],
    with_check: bool,
    line: Iterable[str] = (),
    defended: bool = False,
) -> Motif:
    targets = tuple(targets)
    enemy = not before.turn
    return Motif(
        kind=kind,
        mover=mover,
        attacker=attacker,
        targets=targets,
        with_check=with_check,
        safe=not after.is_attacked_by(enemy, attacker),
        mover_piece=_symbol(after, before, mover),
        attacker_piece=_symbol(after, before, attacker),
        target_pieces=tuple(_enemy_symbol(after, before, t, enemy) for t in targets),
        line=tuple(line),
        defended=defended,
    )


def label_of(motif: Motif) -> str:
    """Korean name shown for this motif. Only hanging_piece has two of them: the target is
    either undefended (무방비 기물) or defended but attacked by something cheaper."""
    if motif.kind == "hanging_piece" and motif.defended:
        return UNDERDEFENDED_NAME
    return KOREAN_NAMES.get(motif.kind, motif.kind)


def _valuable_targets(board: chess.Board, squares: chess.SquareSet) -> tuple[chess.Square, ...]:
    return tuple(sorted(sq for sq in squares if value_at(board, sq) >= VALUABLE))


def _piece_value(board: chess.Board, square: chess.Square) -> int:
    piece_type = board.piece_type_at(square)
    return PIECE_VALUE[piece_type] if piece_type else 0


def _least_valuable(board: chess.Board, squares: chess.SquareSet) -> chess.Square:
    return min(squares, key=lambda s: (_piece_value(board, s), s))


def see(board: chess.Board, target: chess.Square, color: chess.Color) -> int:
    """Static exchange evaluation: material `color` wins by starting captures on `target`,
    never negative (a side may always decline to capture). Captures use the least valuable
    attacker first, x-rays are included, pins are ignored, a king never captures into check."""
    victim = board.piece_type_at(target)
    if victim is None:
        return 0
    probe = board.copy(stack=False)
    victims = [PIECE_VALUE[victim]]
    side = color
    while True:
        attackers = probe.attackers(side, target)
        if not attackers:
            break
        square = _least_valuable(probe, attackers)
        piece = probe.piece_at(square)
        assert piece is not None
        if piece.piece_type == chess.KING and probe.attackers(not side, target):
            break
        probe.remove_piece_at(square)
        probe.set_piece_at(target, piece)
        victims.append(PIECE_VALUE[piece.piece_type])
        side = not side
    captures = len(victims) - 1
    gain = 0
    for i in range(captures - 1, -1, -1):
        gain = max(0, victims[i] - gain)
    return gain


def _can_capture(after: chess.Board, target: chess.Square, color: chess.Color) -> bool:
    """Whether the side that just moved (`color`) could legally capture on `target` next move.
    When the move gave check the opponent answers first and picks the reply that suits it, so
    the threat only holds when no legal reply both parries the check and saves the piece."""
    if after.is_check():
        return all(_still_capturable(after, reply, target, color) for reply in _replies(after))
    probe = after.copy(stack=False)
    probe.push(chess.Move.null())
    return any(probe.generate_legal_moves(to_mask=chess.BB_SQUARES[target]))


def _replies(after: chess.Board) -> list[chess.Move]:
    return list(after.legal_moves)


def _still_capturable(
    after: chess.Board, reply: chess.Move, target: chess.Square, color: chess.Color
) -> bool:
    """After `reply` the piece on `target` is still there and still falls with a material gain."""
    after.push(reply)
    try:
        if after.piece_at(target) is None or value_at(after, target) < VALUABLE:
            return False
        return see(after, target, color) > 0
    finally:
        after.pop()


def _ray_pairs(
    board: chess.Board, square: chess.Square
) -> Iterator[tuple[chess.Square, chess.Square]]:
    """(front, behind): the first two occupied squares along each ray of the slider on
    `square`."""
    piece = board.piece_at(square)
    if piece is None:
        return
    directions: tuple[tuple[int, int], ...] = ()
    if piece.piece_type in (chess.ROOK, chess.QUEEN):
        directions += _ORTHOGONAL
    if piece.piece_type in (chess.BISHOP, chess.QUEEN):
        directions += _DIAGONAL
    for df, dr in directions:
        f, r = chess.square_file(square) + df, chess.square_rank(square) + dr
        front: chess.Square | None = None
        while 0 <= f < 8 and 0 <= r < 8:
            s = chess.square(f, r)
            if board.piece_at(s) is not None:
                if front is None:
                    front = s
                else:
                    yield front, s
                    break
            f += df
            r += dr


def _sliders(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    return (
        board.pieces(chess.ROOK, color)
        | board.pieces(chess.BISHOP, color)
        | board.pieces(chess.QUEEN, color)
    )


def _new_ray_pairs(
    before: chess.Board, after: chess.Board, square: chess.Square, move: chess.Move
) -> Iterator[tuple[chess.Square, chess.Square]]:
    """Ray pairs of the slider on `square` after the move that did not already exist before.
    When the slider is the piece that just moved its new square was empty before, so the
    comparison is against the pairs it held from its old square: a pin or skewer the slider
    already had while sliding along the same line is not created by the move."""
    old: set[tuple[chess.Square, chess.Square]] = set()
    if square == move.to_square:
        old = set(_ray_pairs(before, move.from_square))
    elif before.piece_at(square) == after.piece_at(square):
        old = set(_ray_pairs(before, square))
    for pair in _ray_pairs(after, square):
        if pair not in old:
            yield pair


def _mating_moves(after: chess.Board) -> list[tuple[chess.Move, str]]:
    """Moves of the side that just moved that would checkmate if the opponent passed."""
    if after.is_check() or after.is_game_over():
        return []
    probe = after.copy(stack=False)
    probe.push(chess.Move.null())
    mates: list[tuple[chess.Move, str]] = []
    for move in probe.legal_moves:
        probe.push(move)
        is_mate = probe.is_checkmate()
        probe.pop()
        if is_mate:
            mates.append((move, probe.san(move)))
    return mates


def _is_hanging(board: chess.Board, target: chess.Square, color: chess.Color) -> bool:
    """The enemy piece on `target` is attacked by `color` and either undefended or attacked
    by a piece worth less than itself."""
    attackers = board.attackers(color, target)
    if not attackers:
        return False
    if not board.attackers(not color, target):
        return True
    return _piece_value(board, _least_valuable(board, attackers)) < _piece_value(board, target)


# ---------- detectors ----------


def _vacated(board: chess.Board, move: chess.Move) -> set[chess.Square]:
    """Squares the move empties: the square the piece left plus, for an en-passant capture,
    the square of the pawn taken next to it."""
    squares = {move.from_square}
    if board.is_en_passant(move):
        squares.add(
            chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        )
    return squares


def discovered_attacks(board: chess.Board, move: chess.Move) -> list[Motif]:
    """Sliders of the mover's colour that newly attack a valuable enemy piece through a
    square the move vacated."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    enemy = after.occupied_co[not color]
    vacated = _vacated(board, move)
    found: list[Motif] = []
    for sq in _sliders(after, color):
        if sq == move.to_square:
            continue
        new_attacks = after.attacks(sq) & ~board.attacks(sq) & enemy
        through_vacated = chess.SquareSet(
            t for t in new_attacks if vacated & set(chess.SquareSet(chess.between(sq, t)))
        )
        targets = _valuable_targets(after, through_vacated)
        if targets:
            found.append(
                _make(
                    "discovered_attack",
                    board,
                    after,
                    mover=move.to_square,
                    attacker=sq,
                    targets=targets,
                    with_check=after.is_check(),
                )
            )
    return found


def _fork_wins_something(
    after: chess.Board, color: chess.Color, forker: chess.Square, spoils: tuple[chess.Square, ...]
) -> bool:
    """A fork only deserves the name when one of the pieces it hits can actually be taken:
    a target that falls with a material gain, or one worth more than the forking piece.
    When the fork gives check the opponent answers first, so a target has to survive every
    legal reply (otherwise the check is simply parried by saving or by taking the forker)."""
    if after.is_check():
        return all(
            any(_still_capturable(after, reply, t, color) for t in spoils)
            for reply in _replies(after)
        )
    forker_value = _piece_value(after, forker)
    return any(see(after, t, color) > 0 or _piece_value(after, t) > forker_value for t in spoils)


def forks(board: chess.Board, move: chess.Move) -> list[Motif]:
    """The moved piece attacks two or more valuable enemy pieces (the king counts) and wins
    at least one of them: two attacked pieces alone are geometry, not a fork."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    attacked = after.attacks(move.to_square) & after.occupied_co[not color]
    targets = _valuable_targets(after, attacked)
    if len(targets) < 2:
        return []
    spoils = tuple(t for t in targets if after.piece_type_at(t) != chess.KING)
    if not spoils or not _fork_wins_something(after, color, move.to_square, spoils):
        return []
    return [
        _make(
            "fork",
            board,
            after,
            mover=move.to_square,
            attacker=move.to_square,
            targets=targets,
            with_check=after.is_check(),
        )
    ]


def pins(board: chess.Board, move: chess.Move) -> list[Motif]:
    """After the move a slider of the mover pins an enemy piece (minor or better) to a more
    valuable piece or to the king. Targets are [pinned, behind]; absolute pins carry
    with_check False because a pin against the king is not a check."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    enemy = chess.SquareSet(after.occupied_co[not color])
    found: list[Motif] = []
    for sq in _sliders(after, color):
        for front, behind in _new_ray_pairs(board, after, sq, move):
            if front not in enemy or behind not in enemy:
                continue
            front_type = after.piece_type_at(front)
            behind_type = after.piece_type_at(behind)
            if front_type == chess.KING or _piece_value(after, front) < VALUABLE:
                continue
            absolute = behind_type == chess.KING
            if not absolute and _piece_value(after, behind) <= _piece_value(after, front):
                continue
            found.append(
                _make(
                    "pin",
                    board,
                    after,
                    mover=move.to_square,
                    attacker=sq,
                    targets=(front, behind),
                    with_check=False if absolute else after.is_check(),
                )
            )
    return found


def skewers(board: chess.Board, move: chess.Move) -> list[Motif]:
    """After the move a slider of the mover attacks an enemy piece with a less valuable piece
    (minor or better) behind it on the same line. Targets are [front, behind]."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    enemy = chess.SquareSet(after.occupied_co[not color])
    found: list[Motif] = []
    for sq in _sliders(after, color):
        for front, behind in _new_ray_pairs(board, after, sq, move):
            if front not in enemy or behind not in enemy:
                continue
            if after.piece_type_at(behind) == chess.KING:
                continue
            if _piece_value(after, behind) < VALUABLE:
                continue
            if _piece_value(after, front) <= _piece_value(after, behind):
                continue
            found.append(
                _make(
                    "skewer",
                    board,
                    after,
                    mover=move.to_square,
                    attacker=sq,
                    targets=(front, behind),
                    with_check=after.is_check(),
                )
            )
    return found


def hanging_pieces(board: chess.Board, move: chess.Move) -> list[Motif]:
    """The move creates an attack on an enemy piece (minor or better) that is undefended or
    attacked by a piece worth less than itself, and capturing it wins material."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    found: list[Motif] = []
    for t in chess.SquareSet(after.occupied_co[not color]):
        if after.piece_type_at(t) == chess.KING or _piece_value(after, t) < VALUABLE:
            continue
        new_attackers = after.attackers(color, t) & ~board.attackers(color, t)
        if t in board.attacks(move.from_square):
            new_attackers &= ~chess.BB_SQUARES[move.to_square]
        if not new_attackers:
            continue
        if _is_hanging(board, t, color):
            continue  # it was already hanging before the move
        if not _is_hanging(after, t, color):
            continue
        if see(after, t, color) <= 0 or not _can_capture(after, t, color):
            continue
        attacker = move.to_square if move.to_square in new_attackers else min(new_attackers)
        found.append(
            _make(
                "hanging_piece",
                board,
                after,
                mover=move.to_square,
                attacker=attacker,
                targets=(t,),
                with_check=after.is_check(),
                defended=bool(after.attackers(not color, t)),
            )
        )
    return found


def remove_defenders(board: chess.Board, move: chess.Move) -> list[Motif]:
    """The move captures or newly attacks the only defender of an enemy piece that the mover
    attacks. Targets are [defender, protected piece]."""
    _require_legal(board, move)
    color = board.turn
    enemy = not color
    after = _after(board, move)
    mover_value = _piece_value(after, move.to_square)
    found: list[Motif] = []
    for d in chess.SquareSet(board.occupied_co[enemy]):
        if board.piece_type_at(d) == chess.KING:
            continue
        captured = move.to_square == d
        need_gain = 0
        if captured:
            # A capture that loses material on its own (queen takes a defended knight) only
            # counts when the piece it unprotects is worth more than that loss.
            if board.attackers(enemy, d):
                need_gain = max(0, mover_value - _piece_value(board, d))
        else:
            if after.piece_at(d) is None:
                continue
            if board.is_attacked_by(color, d) or not after.is_attacked_by(color, d):
                continue
            if see(after, d, color) <= 0:
                continue
        for p in chess.SquareSet(board.occupied_co[enemy]):
            if p == d or board.piece_type_at(p) == chess.KING:
                continue
            if _piece_value(board, p) < VALUABLE or _piece_value(board, p) <= need_gain:
                continue
            if board.attackers(enemy, p) != chess.SquareSet([d]):
                continue
            if not after.is_attacked_by(color, p):
                continue
            defenders_after = after.attackers(enemy, p)
            if captured and defenders_after:
                continue
            if not captured and defenders_after != chess.SquareSet([d]):
                continue
            found.append(
                _make(
                    "remove_defender",
                    board,
                    after,
                    mover=move.to_square,
                    attacker=move.to_square,
                    targets=(d, p),
                    with_check=after.is_check(),
                )
            )
    return found


def overloads(board: chess.Board, move: chess.Move) -> list[Motif]:
    """An enemy piece is the only defender of two pieces; the move attacks one of them so that
    capturing it drags the defender away from the other. Targets are
    [defender, newly attacked piece, the piece that then falls]."""
    _require_legal(board, move)
    color = board.turn
    enemy = not color
    after = _after(board, move)
    found: list[Motif] = []
    for d in chess.SquareSet(after.occupied_co[enemy]):
        if after.piece_type_at(d) == chess.KING:
            continue
        protected = [
            p
            for p in chess.SquareSet(after.occupied_co[enemy])
            if p != d
            and after.piece_type_at(p) != chess.KING
            and after.attackers(enemy, p) == chess.SquareSet([d])
            and after.is_attacked_by(color, p)
        ]
        if len(protected) < 2:
            continue
        for p1 in protected:
            newly = after.attackers(color, p1) & ~board.attackers(color, p1)
            if p1 in board.attacks(move.from_square):
                newly &= ~chess.BB_SQUARES[move.to_square]
            if not newly:
                continue
            cheapest = _piece_value(after, _least_valuable(after, after.attackers(color, p1)))
            if cheapest > _piece_value(after, p1):
                continue
            for p2 in protected:
                if p2 == p1:
                    continue
                if max(_piece_value(after, p1), _piece_value(after, p2)) < VALUABLE:
                    continue
                attacker = move.to_square if move.to_square in newly else min(newly)
                found.append(
                    _make(
                        "overload",
                        board,
                        after,
                        mover=move.to_square,
                        attacker=attacker,
                        targets=(d, p1, p2),
                        with_check=after.is_check(),
                    )
                )
                break
            else:
                continue
            break
    return found


def _boxed_on_back_rank(board: chess.Board, king: chess.Square, color: chess.Color) -> bool:
    """`color`'s king stands on its back rank and every square in front of it is occupied by
    its own pieces, at least one of them a pawn."""
    back_rank = 0 if color == chess.WHITE else 7
    if chess.square_rank(king) != back_rank:
        return False
    step = 1 if color == chess.WHITE else -1
    own = chess.SquareSet(board.occupied_co[color])
    pawns = 0
    kf = chess.square_file(king)
    for f in (kf - 1, kf, kf + 1):
        if not 0 <= f < 8:
            continue
        sq = chess.square(f, back_rank + step)
        if sq not in own:
            return False
        if board.piece_type_at(sq) == chess.PAWN:
            pawns += 1
    return pawns >= 1


def back_rank(board: chess.Board, move: chess.Move) -> list[Motif]:
    """After the move the mover threatens mate on the enemy back rank against a king boxed in
    by its own pieces. `line` holds the mating move."""
    _require_legal(board, move)
    color = board.turn
    enemy = not color
    after = _after(board, move)
    king = after.king(enemy)
    if king is None or not _boxed_on_back_rank(after, king, enemy):
        return []
    back = chess.BB_RANK_1 if enemy == chess.WHITE else chess.BB_RANK_8
    found: list[Motif] = []
    seen: set[chess.Square] = set()
    for mate, san in _mating_moves(after):
        if not chess.BB_SQUARES[mate.to_square] & back:
            continue
        if after.piece_type_at(mate.from_square) not in (chess.ROOK, chess.QUEEN):
            continue
        if mate.from_square in seen:
            continue
        seen.add(mate.from_square)
        found.append(
            _make(
                "back_rank",
                board,
                after,
                mover=move.to_square,
                attacker=mate.from_square,
                targets=(king,),
                with_check=after.is_check(),
                line=(san,),
            )
        )
    return found


def trapped_pieces(board: chess.Board, move: chess.Move) -> list[Motif]:
    """The move attacks an enemy piece (minor or better) that has no safe square: after every
    legal reply it can still be captured with a material gain."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    if after.is_check():
        return []  # a piece lost to a check is a different story from a piece with no squares
    found: list[Motif] = []
    for t in chess.SquareSet(after.occupied_co[not color]):
        piece_type = after.piece_type_at(t)
        if piece_type == chess.KING or PIECE_VALUE[piece_type or chess.PAWN] < VALUABLE:
            continue
        if not after.is_attacked_by(color, t) or see(after, t, color) <= 0:
            continue
        if board.is_attacked_by(color, t) and see(board, t, color) > 0:
            continue  # it was already lost before the move
        if not _can_capture(after, t, color):
            continue
        if _can_be_saved(after, t, color):
            continue
        attackers = after.attackers(color, t)
        attacker = (
            move.to_square if move.to_square in attackers else _least_valuable(after, attackers)
        )
        found.append(
            _make(
                "trapped_piece",
                board,
                after,
                mover=move.to_square,
                attacker=attacker,
                targets=(t,),
                with_check=after.is_check(),
            )
        )
    return found


def _can_be_saved(after: chess.Board, t: chess.Square, color: chess.Color) -> bool:
    """Whether the opponent (to move in `after`) has a reply after which the piece from `t`
    is no longer attacked with a gain, or it sells itself for equal material."""
    own_value = _piece_value(after, t)
    for reply in after.legal_moves:
        if reply.from_square == t:
            captured = _piece_value(after, reply.to_square)
            if captured >= own_value:
                return True
        after.push(reply)
        try:
            square = reply.to_square if reply.from_square == t else t
            if after.piece_at(square) is None:
                continue
            if after.is_checkmate():
                return True
            if not after.is_attacked_by(color, square) or see(after, square, color) <= 0:
                return True
        finally:
            after.pop()
    return False


def mate_threats(board: chess.Board, move: chess.Move) -> list[Motif]:
    """After the move the mover would mate in one if given a free move. One motif per piece
    that can deliver the mate; `line` holds the mating move."""
    _require_legal(board, move)
    color = board.turn
    after = _after(board, move)
    king = after.king(not color)
    if king is None:
        return []
    found: list[Motif] = []
    seen: set[chess.Square] = set()
    for mate, san in _mating_moves(after):
        if mate.from_square in seen:
            continue
        seen.add(mate.from_square)
        found.append(
            _make(
                "mate_threat",
                board,
                after,
                mover=move.to_square,
                attacker=mate.from_square,
                targets=(king,),
                with_check=after.is_check(),
                line=(san,),
            )
        )
    return found


DETECTORS = (
    discovered_attacks,
    forks,
    pins,
    skewers,
    hanging_pieces,
    remove_defenders,
    overloads,
    back_rank,
    trapped_pieces,
    mate_threats,
)


def _dedupe(found: list[Motif]) -> list[Motif]:
    """Drop the weaker of two motifs about the same fact: a back-rank mate is also a mate
    threat, a trapped piece is also hanging, and a fork or discovery already explains why
    its target falls, so a hanging-piece or removed-defender note on that target is noise."""
    back_rank_lines = {m.line for m in found if m.kind == "back_rank"}
    trapped = {m.targets[0] for m in found if m.kind == "trapped_piece"}
    primary = {t for m in found if m.kind in ("fork", "discovered_attack") for t in m.targets}
    named = {
        (m.attacker, t)
        for m in found
        if m.kind in ("fork", "discovered_attack", "remove_defender", "overload")
        for t in m.targets
    }
    kept: list[Motif] = []
    for m in found:
        if m.kind == "mate_threat" and m.line in back_rank_lines:
            continue
        if m.kind == "hanging_piece" and (
            m.targets[0] in trapped or (m.attacker, m.targets[0]) in named
        ):
            continue
        if m.kind == "remove_defender" and m.targets[1] in primary:
            continue
        kept.append(m)
    return kept


def detect(board: chess.Board, move: chess.Move) -> list[Motif]:
    found: list[Motif] = []
    for detector in DETECTORS:
        found.extend(detector(board, move))
    return _dedupe(found)


# ---------- Korean rendering ----------

_CONSONANT_DIGITS = "013678"  # 영 일 삼 육 칠 팔 end in a consonant


def _ends_with_consonant(word: str) -> bool:
    if not word:
        return False
    last = word[-1]
    if last.isdigit():
        return last in _CONSONANT_DIGITS
    code = ord(last) - 0xAC00
    if 0 <= code < 11172:
        return code % 28 != 0
    return False


def _particle(word: str, with_final: str, without_final: str) -> str:
    return with_final if _ends_with_consonant(word) else without_final


def _josa(word: str, with_final: str, without_final: str) -> str:
    return word + _particle(word, with_final, without_final)


def _piece_square(piece: str, square: chess.Square) -> str:
    if piece == "P":
        return f"{chess.square_name(square)} 폰"
    return f"{piece}{chess.square_name(square)}"


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + _particle(items[-2], "과 ", "와 ") + items[-1]


def describe(motif: Motif) -> str:
    """Short Korean phrase built only from the squares and piece letters in the motif."""
    name = label_of(motif)
    targets = [
        _piece_square(p, sq) for p, sq in zip(motif.target_pieces, motif.targets, strict=False)
    ] or [chess.square_name(sq) for sq in motif.targets]
    attacker = _piece_square(motif.attacker_piece, motif.attacker)
    mover = _piece_square(motif.mover_piece, motif.mover)
    match motif.kind:
        case "fork":
            piece = PIECE_KOREAN.get(motif.attacker_piece, "")
            head = f"{piece} 포크" if piece else name
            return f"{head}: {_join(targets)}"
        case "discovered_attack":
            body = (
                f"{_josa(mover, '이', '가')} 비켜서며 {_josa(attacker, '이', '가')} "
                f"{_josa(_join(targets), '을', '를')} 겨냥"
            )
            return f"{name}: {body}"
        case "pin" if len(targets) == 2:
            absolute = len(motif.target_pieces) == 2 and motif.target_pieces[1] == "K"
            head = "절대 핀" if absolute else name
            return (
                f"{head}: {_josa(attacker, '이', '가')} {_josa(targets[0], '을', '를')} "
                f"{targets[1]}에 묶음"
            )
        case "skewer" if len(targets) == 2:
            return (
                f"{name}: {_josa(attacker, '이', '가')} {_josa(targets[0], '을', '를')} 공격, "
                f"뒤에 {targets[1]}"
            )
        case "hanging_piece":
            return f"{name}: {_josa(attacker, '이', '가')} {_josa(targets[0], '을', '를')} 공격"
        case "remove_defender" if len(targets) == 2:
            if motif.mover == motif.targets[0]:
                return (
                    f"{name}: {_josa(targets[1], '을', '를')} 지키던 "
                    f"{_josa(targets[0], '을', '를')} 잡음"
                )
            return (
                f"{name}: {_josa(attacker, '이', '가')} {_josa(targets[1], '을', '를')} 지키는 "
                f"{_josa(targets[0], '을', '를')} 공격"
            )
        case "overload" if len(targets) == 3:
            return (
                f"{name}: {_josa(targets[0], '이', '가')} {_josa(targets[1], '과', '와')} "
                f"{_josa(targets[2], '을', '를')} 함께 지킴, "
                f"{_josa(attacker, '이', '가')} {_josa(targets[1], '을', '를')} 공격"
            )
        case "back_rank" if motif.line:
            return f"{name}: {motif.line[0]} 위협"
        case "trapped_piece":
            return (
                f"{name}: {_josa(attacker, '이', '가')} {_josa(targets[0], '을', '를')} 공격, "
                f"피할 칸 없음"
            )
        case "mate_threat" if motif.line:
            return f"{name}: {motif.line[0]}"
    return f"{name}: {_join(targets)}" if targets else name
