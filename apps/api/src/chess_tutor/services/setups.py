"""Setup (system opening) knowledge base and reachability rules (layer 3, plan §5.4).

A *setup* is a system opening: the fixed arrangement of pawns and pieces a player heads for
almost regardless of what the opponent replies — the London, the Colle, the King's Indian
Attack, the Hedgehog. `services/plans.py` describes middlegame pawn structures and stays
silent this early (`structure.classify` needs six pawns gone before it names a structure), so
this module fills the opening-phase gap: for any position it says how far each setup has come,
what is left, and what put it out of reach.

Every judgement is deterministic and ignores the opponent's replies:

* a **pawn** target is reachable when an own pawn stands behind it on the same file with
  nothing in between;
* a **piece** target is reachable when a piece of that type gets there in at most two quiet
  moves over empty squares, and no two targets may claim the same piece;
* a **castling** target needs the castling right;
* a pawn that has not left its home rank is not an obstacle — it can still step aside — so the
  starting position leaves every setup ``possible``;
* a ``before`` pair names the target that has to come first (Bf4 before e3, or the c1 bishop
  is shut in behind its own pawn chain).

Prose rule as in `services/plans.py`: entries name squares and pieces only, never a colour
word, so a setup written for one side reads the same as the one written for the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import chess

from chess_tutor.schemas import Color, SetupStatus

Side = Color
TargetState = Literal["done", "possible", "blocked"]

REACH_PLIES = 2
"""Quiet moves a piece may take to reach its target square (plan §5.4)."""

PIECE_NAMES_KO: dict[chess.PieceType, str] = {
    chess.KING: "킹",
    chess.QUEEN: "퀸",
    chess.ROOK: "룩",
    chess.BISHOP: "비숍",
    chess.KNIGHT: "나이트",
    chess.PAWN: "폰",
}

FILE_NAMES = "abcdefgh"


@dataclass(frozen=True)
class SetupSpec:
    """One system opening: where the pieces go, in which order, and what for."""

    id: str
    name: str
    side: Side
    targets: tuple[tuple[str, chess.PieceType], ...]
    """(square, piece type) pairs in the order a player normally fills them."""
    before: tuple[tuple[str, str], ...] = ()
    """(piece square, pawn square): the piece target has to be filled before that pawn move."""
    plans: tuple[str, ...] = ()
    """Typical plans once the setup stands, written like the titles in plans.PLANS."""
    against: str | None = None
    """When the setup applies, as a family of opponent moves."""


P, N, B, K = chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.KING
"""Short names for the knowledge base below, so a setup reads like the line it comes from."""


SETUPS: tuple[SetupSpec, ...] = (
    SetupSpec(
        id="london",
        name="런던 시스템",
        side="white",
        targets=(("d4", P), ("f4", B), ("e3", P), ("f3", N), ("c3", P), ("d2", N), ("d3", B)),
        before=(("f4", "e3"),),
        plans=(
            "Ne5 뒤 Qf3·Qh4로 킹사이드 공격",
            "c4 브레이크로 d5 폰 압박",
            "h3와 Bh2로 f4 비숍 지키기",
        ),
        against="…d5 계열",
    ),
    SetupSpec(
        id="colle_zukertort",
        name="콜레-쥐케르토르트",
        side="white",
        targets=(("d4", P), ("f3", N), ("e3", P), ("d3", B), ("b3", P), ("b2", B), ("d2", N)),
        plans=(
            "Ne5와 f4로 킹사이드에 병력 모으기",
            "c4 브레이크로 b2 비숍의 대각선 열기",
        ),
        against="…d5 계열",
    ),
    SetupSpec(
        id="colle_koltanowski",
        name="콜레-콜타노프스키",
        side="white",
        targets=(("d4", P), ("f3", N), ("e3", P), ("d3", B), ("c3", P), ("d2", N)),
        plans=(
            "e4 브레이크로 중앙 열기",
            "e5 전진 뒤 Ng5·Qh5 킹사이드 공격",
        ),
        against="…d5 계열",
    ),
    SetupSpec(
        id="stonewall_attack",
        name="스톤월 어택",
        side="white",
        targets=(("d4", P), ("e3", P), ("f4", P), ("d3", B), ("f3", N), ("d2", N)),
        plans=(
            "Ne5 말뚝 뒤 Rf3-h3로 h7 겨냥",
            "g4-g5로 f6 나이트 밀어내기",
        ),
        against="…d5 계열",
    ),
    SetupSpec(
        id="kia",
        name="킹스 인디언 어택",
        side="white",
        targets=(("f3", N), ("g3", P), ("g2", B), ("g1", K), ("d3", P), ("d2", N), ("e4", P)),
        plans=(
            "e5 쐐기 뒤 Nf1-h2와 g4로 킹사이드 공격",
            "Re1·Qe2로 e4 폰을 받치고 중앙 압박",
        ),
        against="…e6 계열, …c5 계열",
    ),
    SetupSpec(
        id="torre",
        name="토레 어택",
        side="white",
        targets=(("d4", P), ("f3", N), ("g5", B), ("e3", P), ("c3", P), ("d2", N), ("d3", B)),
        before=(("g5", "e3"),),
        plans=(
            "Bxf6 뒤 e4로 중앙 차지",
            "Ne5와 f4로 킹사이드 압박",
        ),
        against="…Nf6 계열",
    ),
    SetupSpec(
        id="reti",
        name="레티 더블 피안케토",
        side="white",
        targets=(("f3", N), ("g3", P), ("g2", B), ("b3", P), ("b2", B), ("g1", K)),
        plans=(
            "c4와 d3로 d5 폰을 옆에서 누르기",
            "e4 브레이크로 두 비숍의 대각선 열기",
        ),
        against="…d5 계열",
    ),
    SetupSpec(
        id="maroczy",
        name="마로치 바인드",
        side="white",
        targets=(("e4", P), ("c4", P), ("c3", N), ("e2", B), ("e3", B), ("d4", N)),
        plans=(
            "c4·e4 폰으로 d5 칸 봉쇄",
            "Rc1과 b4-b5로 c파일 압박",
            "f3와 Qd2로 킹사이드 전개 늦추기",
        ),
        against="…c5 계열",
    ),
    SetupSpec(
        id="kid",
        name="킹스 인디언 배치",
        side="black",
        targets=(("f6", N), ("g6", P), ("g7", B), ("d6", P), ("g8", K)),
        plans=(
            "…e5 뒤 …f5로 킹사이드 열기",
            "…c5로 중앙에 도전",
        ),
        against="d4 계열",
    ),
    SetupSpec(
        id="pirc",
        name="피르츠/모던 배치",
        side="black",
        targets=(("d6", P), ("f6", N), ("g6", P), ("g7", B)),
        plans=(
            "…e5 또는 …c5로 중앙에 도전",
            "…Nc6와 …Bg4로 d4 폰 압박",
        ),
        against="e4 계열",
    ),
    SetupSpec(
        id="hedgehog",
        name="헤지호그",
        side="black",
        targets=(
            ("a6", P),
            ("b6", P),
            ("b7", B),
            ("d6", P),
            ("e6", P),
            ("e7", B),
            ("d7", N),
        ),
        plans=(
            "…d5 브레이크 준비",
            "…b5 브레이크로 c4 폰 흔들기",
            "…Qb8·…Rc8로 폰 뒤에 기물 정렬",
        ),
        against="c4와 e4를 함께 세운 배치",
    ),
    SetupSpec(
        id="stonewall_dutch",
        name="스톤월 더치",
        side="black",
        targets=(("f5", P), ("e6", P), ("d5", P), ("c6", P), ("d6", B)),
        plans=(
            "…Ne4 말뚝 뒤 …Rf6-h6 킹사이드 공격",
            "…g5로 f파일 열기",
        ),
        against="d4 계열",
    ),
    SetupSpec(
        id="leningrad_dutch",
        name="레닌그라드 더치",
        side="black",
        targets=(("f5", P), ("f6", N), ("g6", P), ("g7", B), ("d6", P), ("g8", K)),
        plans=(
            "…e5 브레이크로 중앙 열기",
            "…Qe8-h5와 …f4로 킹사이드 공격",
        ),
        against="d4 계열",
    ),
    SetupSpec(
        id="qid",
        name="퀸즈 인디언 배치",
        side="black",
        targets=(("f6", N), ("e6", P), ("b6", P), ("b7", B), ("e7", B)),
        plans=(
            "…Ne4와 …f5로 e4 칸 다투기",
            "…d5로 중앙을 굳히기",
        ),
        against="d4·Nf3 계열",
    ),
)
"""White 8, Black 6. Black setups are written in their own orientation, never mirrored from a
White entry, because they arise in Black positions to begin with."""


@dataclass(frozen=True)
class TargetStatus:
    """One square of a setup and why it is where it is."""

    square: str
    piece_type: chess.PieceType
    label: str
    state: TargetState
    why: str | None = None


@dataclass(frozen=True)
class SetupChange:
    """What one move did to a setup: filled another square, or ruled the setup out."""

    kind: Literal["advance", "blocked"]
    setup_id: str
    name: str
    done: int
    total: int
    why: str | None = None


# ---------- Korean helpers ----------


def _subject(word: str) -> str:
    """'이' or '가' after `word`, by the final consonant of its last Hangul syllable."""
    last = word[-1]
    if "가" <= last <= "힣":
        return "이" if (ord(last) - 0xAC00) % 28 else "가"
    return "이"


# ---------- board helpers ----------


def _color(side: Side) -> chess.Color:
    return chess.WHITE if side == "white" else chess.BLACK


def target_label(square: str, piece_type: chess.PieceType) -> str:
    """'d4' for a pawn, 'O-O' for a castled king, 'Bf4' for anything else."""
    if piece_type == chess.PAWN:
        return square
    if piece_type == chess.KING:
        return "O-O" if square[0] in "gh" else "O-O-O"
    return chess.piece_symbol(piece_type).upper() + square


def _free_board(board: chess.Board, color: chess.Color) -> chess.Board:
    """The board with this side's untouched pawns lifted off.

    A pawn still on its home rank can step aside on its own, so it never stops a piece from
    reaching its target square; a pawn that has already committed itself does."""
    free = board.copy(stack=False)
    home = 1 if color == chess.WHITE else 6
    for square in list(board.pieces(chess.PAWN, color)):
        if chess.square_rank(square) == home:
            free.remove_piece_at(square)
    return free


def _reaches(
    free: chess.Board,
    color: chess.Color,
    piece_type: chess.PieceType,
    start: chess.Square,
    target: chess.Square,
) -> bool:
    """Whether a `piece_type` on `start` walks to `target` in at most REACH_PLIES quiet moves.

    Quiet means every square it lands on is empty on `free`; the opponent never moves. Sliding
    pieces are stopped by whatever is left standing there."""
    scratch = free.copy(stack=False)
    scratch.remove_piece_at(start)
    piece = chess.Piece(piece_type, color)
    frontier = {start}
    seen = {start}
    for _ in range(REACH_PLIES):
        following: set[chess.Square] = set()
        for square in frontier:
            scratch.set_piece_at(square, piece)
            steps = {s for s in scratch.attacks(square) if scratch.piece_at(s) is None}
            scratch.remove_piece_at(square)
            if target in steps:
                return True
            following |= steps - seen
        seen |= following
        frontier = following
    return False


def _pawn_path(board: chess.Board, color: chess.Color, target: chess.Square) -> str | None:
    """None when an own pawn can still march to `target`, else why it cannot.

    The nearest own pawn behind it on the file is the one that would go; a pawn that is already
    past the square, or a piece standing in its way, ends the setup."""
    file_index = chess.square_file(target)
    target_rank = chess.square_rank(target)
    step = -1 if color == chess.WHITE else 1
    behind = list(range(target_rank + step, -1 if step < 0 else 8, step))
    pawn_rank = next(
        (
            rank
            for rank in behind
            if (piece := board.piece_at(chess.square(file_index, rank))) is not None
            and piece.color == color
            and piece.piece_type == chess.PAWN
        ),
        None,
    )
    if pawn_rank is None:
        return f"{FILE_NAMES[file_index]}파일 폰이 이미 지나갔거나 없습니다"
    for rank in behind:
        if rank == pawn_rank:
            break
        square = chess.square(file_index, rank)
        if board.piece_at(square) is not None:
            return (
                f"{chess.square_name(square)}에 기물이 있어 "
                f"{chess.square_name(target)}까지 폰이 지나갈 수 없습니다"
            )
    return None


def _home_square(
    target: chess.Square, piece_type: chess.PieceType, color: chess.Color
) -> chess.Square | None:
    """Starting square of the piece that would normally take `target` (a bishop is picked by
    the colour of the square it lives on)."""
    start = chess.Board()
    for square in sorted(start.pieces(piece_type, color)):
        if piece_type == chess.BISHOP and _dark(square) != _dark(target):
            continue
        return square
    return None


def _dark(square: chess.Square) -> bool:
    return (chess.square_file(square) + chess.square_rank(square)) % 2 == 0


def _match(candidates: list[list[chess.Square]]) -> list[chess.Square | None]:
    """Give each target its own piece where the choices allow it (augmenting paths).

    Two knight targets need two knights: a piece that another target already claimed cannot
    count twice."""
    taken: dict[chess.Square, int] = {}

    def assign(index: int, seen: set[chess.Square]) -> bool:
        for square in candidates[index]:
            if square in seen:
                continue
            seen.add(square)
            holder = taken.get(square)
            if holder is None or assign(holder, seen):
                taken[square] = index
                return True
        return False

    for index in range(len(candidates)):
        assign(index, set())
    out: list[chess.Square | None] = [None] * len(candidates)
    for square, index in taken.items():
        out[index] = square
    return out


# ---------- one setup ----------


def _evaluate(spec: SetupSpec, board: chess.Board) -> list[TargetStatus]:
    """State of every target square of `spec`, in the order the knowledge base lists them."""
    color = _color(spec.side)
    free = _free_board(board, color)
    squares = [chess.parse_square(name) for name, _ in spec.targets]
    done = [
        (piece := board.piece_at(square)) is not None
        and piece.color == color
        and piece.piece_type == piece_type
        for square, (_, piece_type) in zip(squares, spec.targets, strict=True)
    ]
    filled = {square for square, is_done in zip(squares, done, strict=True) if is_done}

    # Pieces that have to walk somewhere, matched one-to-one against the pieces available.
    walkers = [
        index
        for index, (_, piece_type) in enumerate(spec.targets)
        if not done[index] and piece_type not in (chess.PAWN, chess.KING)
    ]
    options = [
        [
            start
            for start in sorted(board.pieces(spec.targets[index][1], color))
            if start not in filled
            and _reaches(free, color, spec.targets[index][1], start, squares[index])
        ]
        for index in walkers
    ]
    assigned = dict(zip(walkers, _match(options), strict=True))

    out: list[TargetStatus] = []
    for index, (name, piece_type) in enumerate(spec.targets):
        label = target_label(name, piece_type)
        square = squares[index]
        if done[index]:
            out.append(TargetStatus(name, piece_type, label, "done"))
            continue
        why: str | None
        if piece_type == chess.KING:
            kingside = name[0] in "gh"
            has_right = (
                board.has_kingside_castling_rights(color)
                if kingside
                else board.has_queenside_castling_rights(color)
            )
            why = None if has_right else "캐슬링 권리를 잃었습니다"
        elif piece_type == chess.PAWN:
            occupant = board.piece_at(square)
            why = (
                f"{name}에 다른 기물이 있습니다"
                if occupant is not None
                else _pawn_path(board, color, square)
            )
        elif assigned[index] is not None:
            why = None
        elif free.piece_at(square) is not None:
            why = f"{name}에 다른 기물이 있습니다"
        else:
            piece_ko = PIECE_NAMES_KO[piece_type]
            why = f"{name}에 두 수 안에 닿는 {piece_ko}{_subject(piece_ko)} 없습니다"
        out.append(
            TargetStatus(name, piece_type, label, "possible" if why is None else "blocked", why)
        )

    return _apply_before(spec, board, out)


def _apply_before(
    spec: SetupSpec, board: chess.Board, targets: list[TargetStatus]
) -> list[TargetStatus]:
    """Turn an order violation into the knowledge base's own reason.

    Only fires while the piece still sits at home: a bishop that already came out is not shut
    in by a pawn move behind it."""
    color = _color(spec.side)
    by_square = {target.square: index for index, target in enumerate(targets)}
    for piece_square, pawn_square in spec.before:
        index = by_square.get(piece_square)
        if index is None or targets[index].state == "done":
            continue
        pawn = board.piece_at(chess.parse_square(pawn_square))
        if pawn is None or pawn.color != color or pawn.piece_type != chess.PAWN:
            continue
        piece_type = targets[index].piece_type
        home = _home_square(chess.parse_square(piece_square), piece_type, color)
        if home is None:
            continue
        at_home = board.piece_at(home)
        if at_home is None or at_home.color != color or at_home.piece_type != piece_type:
            continue
        piece_ko = PIECE_NAMES_KO[piece_type]
        targets[index] = TargetStatus(
            targets[index].square,
            piece_type,
            targets[index].label,
            "blocked",
            f"{pawn_square}를 먼저 두어 {chess.square_name(home)} "
            f"{piece_ko}{_subject(piece_ko)} 갇혔습니다",
        )
    return targets


def status(spec: SetupSpec, board: chess.Board) -> SetupStatus:
    """How far this setup has come in this position.

    completed   every target filled
    blocked     at least one target out of reach (the first reason is reported)
    in_progress some targets filled and the rest still reachable
    possible    nothing filled yet and nothing in the way
    """
    targets = _evaluate(spec, board)
    done = [t.label for t in targets if t.state == "done"]
    remaining = [t.label for t in targets if t.state != "done"]
    blocked = next((t for t in targets if t.state == "blocked"), None)
    if not remaining:
        state: Literal["completed", "in_progress", "possible", "blocked"] = "completed"
    elif blocked is not None:
        state = "blocked"
    elif done:
        state = "in_progress"
    else:
        state = "possible"
    return SetupStatus(
        id=spec.id,
        name=spec.name,
        side=spec.side,
        status=state,
        done=done,
        remaining=remaining,
        blocked_by=blocked.why if blocked is not None else None,
        plans=list(spec.plans),
        typical_against=spec.against,
    )


_STATUS_ORDER = {"completed": 0, "in_progress": 1, "possible": 2, "blocked": 3}


def status_all(board: chess.Board) -> list[SetupStatus]:
    """Every setup, the side to move first, then completed → in_progress (fullest first) →
    possible → blocked, ties broken by the order of SETUPS."""
    side: Side = "white" if board.turn == chess.WHITE else "black"
    index = {spec.id: i for i, spec in enumerate(SETUPS)}
    out = [status(spec, board) for spec in SETUPS]
    out.sort(key=lambda s: (s.side != side, _STATUS_ORDER[s.status], -len(s.done), index[s.id]))
    return out


def advance(board_before: chess.Board, move: chess.Move) -> list[SetupChange]:
    """What this move did to the setups of the side that played it.

    'advance' when it filled another target square, 'blocked' when it ruled out a setup that
    was already under way — the more useful sentence of the two for someone learning."""
    if not board_before.is_legal(move):
        raise ValueError("이 국면에서 둘 수 없는 수입니다.")
    side: Side = "white" if board_before.turn == chess.WHITE else "black"
    after = board_before.copy(stack=False)
    after.push(move)
    out: list[SetupChange] = []
    for spec in SETUPS:
        if spec.side != side:
            continue
        was = status(spec, board_before)
        now = status(spec, after)
        total = len(now.done) + len(now.remaining)
        if len(now.done) > len(was.done) and now.status != "blocked":
            out.append(SetupChange("advance", spec.id, spec.name, len(now.done), total))
        elif was.status != "blocked" and now.status == "blocked" and was.done:
            out.append(
                SetupChange("blocked", spec.id, spec.name, len(now.done), total, now.blocked_by)
            )
    return out


def find(setup_id: str) -> SetupSpec | None:
    return next((spec for spec in SETUPS if spec.id == setup_id), None)
