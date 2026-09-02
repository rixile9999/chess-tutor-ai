"""Static positional features (layer 2: concept extraction).

Everything here is computed from the board with python-chess only: no engine, no LLM.
`static_features` measures one position per side; `feature_diff` lines two positions up
from one side's point of view; `summarize_features` compares the two sides of a single
position. The Korean cell strings are assembled from the numbers and squares they report,
so each one can be checked against the board it was built from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import chess
from pydantic import BaseModel

from chess_tutor import schemas
from chess_tutor.values import PIECE_VALUE

Color = Literal["white", "black"]

CENTER = (chess.D4, chess.E4, chess.D5, chess.E5)
FILES = "abcdefgh"

LABEL_MATERIAL = "재료"
LABEL_PAWNS = "폰 구조"
LABEL_ACTIVITY = "기물 활동"
LABEL_KING = "킹 안전"
LABEL_SPACE = "공간"
LABEL_PASSED = "통과폰"
LABEL_FILES = "열린 파일"
LABEL_BISHOPS = "비숍 페어"


class SideFeatures(BaseModel):
    color: Color
    material: int
    """Sum of piece values, king excluded."""
    pawn_count: int
    piece_counts: dict[str, int] = {}
    """Upper-case piece letter -> count (N, B, R, Q)."""
    pawns: list[str] = []
    isolated_pawns: list[str] = []
    doubled_pawns: list[str] = []
    """Every pawn standing on a file that holds two or more own pawns."""
    passed_pawns: list[str] = []
    backward_pawns: list[str] = []
    pawn_islands: int = 0
    open_files: list[str] = []
    """Files without pawns of either colour (the same for both sides)."""
    half_open_files: list[str] = []
    """Files with enemy pawns but no own pawn."""
    rooks_on_open_files: list[str] = []
    """Own rooks and queens standing on an open or half-open file."""
    king_square: str | None = None
    king_pawn_shield: int = 0
    """Own pawns on the king's file and the two neighbouring files, one or two ranks ahead."""
    king_zone_attackers: int = 0
    """Enemy pieces (not pawns) attacking the king's square or a square next to it."""
    mobility: int = 0
    """Squares reachable by knights, bishops, rooks and queens, excluding own pieces and
    squares guarded by enemy pawns."""
    outposts: list[str] = []
    """Own minor pieces on a pawn-protected square on the 4th to 6th rank from this side's
    point of view that no enemy pawn can ever attack."""
    bishop_pair: bool = False
    space: int = 0
    """Squares in the enemy half occupied or attacked by this side."""
    center_control: int = 0
    """Attacks on d4, e4, d5, e5 plus one per own piece standing there."""


class Features(BaseModel):
    white: SideFeatures
    black: SideFeatures
    fen: str

    def side(self, color: Color) -> SideFeatures:
        return self.white if color == "white" else self.black


# ---------- measurement ----------


def _rel_rank(square: chess.Square, color: chess.Color) -> int:
    rank = chess.square_rank(square)
    return rank if color == chess.WHITE else 7 - rank


def _ahead(square: chess.Square, color: chess.Color, files: tuple[int, ...]) -> chess.SquareSet:
    """Squares on `files` strictly in front of `square` from `color`'s point of view."""
    rank = chess.square_rank(square)
    ranks = range(rank + 1, 8) if color == chess.WHITE else range(rank - 1, -1, -1)
    return chess.SquareSet(chess.square(f, r) for r in ranks for f in files if 0 <= f < 8)


def _pawn_attacks(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    attacked = chess.SquareSet()
    for sq in board.pieces(chess.PAWN, color):
        attacked |= board.attacks(sq)
    return attacked


def _all_attacks(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    attacked = chess.SquareSet()
    for sq in chess.SquareSet(board.occupied_co[color]):
        attacked |= board.attacks(sq)
    return attacked


def _names(squares: chess.SquareSet | list[chess.Square]) -> list[str]:
    return [chess.square_name(sq) for sq in sorted(squares)]


def _side_features(board: chess.Board, color: chess.Color) -> SideFeatures:
    enemy = not color
    own_pawns = board.pieces(chess.PAWN, color)
    enemy_pawns = board.pieces(chess.PAWN, enemy)
    own_files = {chess.square_file(sq) for sq in own_pawns}
    enemy_files = {chess.square_file(sq) for sq in enemy_pawns}
    enemy_pawn_attacks = _pawn_attacks(board, enemy)
    own_pawn_attacks = _pawn_attacks(board, color)

    isolated: list[chess.Square] = []
    doubled: list[chess.Square] = []
    passed: list[chess.Square] = []
    backward: list[chess.Square] = []
    for sq in own_pawns:
        f = chess.square_file(sq)
        neighbours = (f - 1, f + 1)
        is_isolated = not any(n in own_files for n in neighbours)
        if is_isolated:
            isolated.append(sq)
        if sum(1 for p in own_pawns if chess.square_file(p) == f) >= 2:
            doubled.append(sq)
        if not (_ahead(sq, color, (f - 1, f, f + 1)) & enemy_pawns):
            passed.append(sq)
        if not is_isolated:
            rel = _rel_rank(sq, color)
            support = [
                p
                for p in own_pawns
                if chess.square_file(p) in neighbours and _rel_rank(p, color) <= rel
            ]
            step = 8 if color == chess.WHITE else -8
            stop = sq + step
            if not support and 0 <= stop < 64:
                if stop in enemy_pawn_attacks or stop in enemy_pawns:
                    backward.append(sq)

    islands = 0
    previous = False
    for f in range(8):
        present = f in own_files
        if present and not previous:
            islands += 1
        previous = present

    open_files = [FILES[f] for f in range(8) if f not in own_files and f not in enemy_files]
    half_open = [FILES[f] for f in range(8) if f not in own_files and f in enemy_files]
    usable = set(open_files) | set(half_open)
    heavy = board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color)
    rooks_on_open = [sq for sq in heavy if FILES[chess.square_file(sq)] in usable]

    king = board.king(color)
    shield = 0
    zone_attackers = 0
    if king is not None:
        kf = chess.square_file(king)
        rel = _rel_rank(king, color)
        for f in (kf - 1, kf, kf + 1):
            if not 0 <= f < 8:
                continue
            for dr in (1, 2):
                r = rel + dr
                if r > 7:
                    continue
                rank = r if color == chess.WHITE else 7 - r
                if chess.square(f, rank) in own_pawns:
                    shield += 1
        zone = chess.SquareSet([king]) | board.attacks(king)
        attackers = chess.SquareSet()
        for sq in zone:
            attackers |= board.attackers(enemy, sq)
        zone_attackers = sum(
            1 for sq in attackers if board.piece_type_at(sq) not in (chess.PAWN, chess.KING)
        )

    own_occupied = chess.SquareSet(board.occupied_co[color])
    mobility = 0
    for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        for sq in board.pieces(piece_type, color):
            mobility += len(board.attacks(sq) & ~own_occupied & ~enemy_pawn_attacks)

    outposts: list[chess.Square] = []
    minors = board.pieces(chess.KNIGHT, color) | board.pieces(chess.BISHOP, color)
    for sq in minors:
        f = chess.square_file(sq)
        if not 3 <= _rel_rank(sq, color) <= 5:
            continue
        if sq not in own_pawn_attacks:
            continue
        if _ahead(sq, color, (f - 1, f + 1)) & enemy_pawns:
            continue
        outposts.append(sq)

    bishops = board.pieces(chess.BISHOP, color)
    bishop_pair = bool(bishops & chess.BB_LIGHT_SQUARES) and bool(bishops & chess.BB_DARK_SQUARES)

    enemy_half = chess.SquareSet(sq for sq in chess.SQUARES if _rel_rank(sq, color) >= 4)
    space = len((_all_attacks(board, color) | own_occupied) & enemy_half)

    center_control = 0
    for sq in CENTER:
        center_control += len(board.attackers(color, sq))
        if sq in own_occupied:
            center_control += 1

    material = sum(
        PIECE_VALUE[pt] * len(board.pieces(pt, color))
        for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
    )
    counts = {
        "N": len(board.pieces(chess.KNIGHT, color)),
        "B": len(board.pieces(chess.BISHOP, color)),
        "R": len(board.pieces(chess.ROOK, color)),
        "Q": len(board.pieces(chess.QUEEN, color)),
    }

    return SideFeatures(
        color="white" if color == chess.WHITE else "black",
        material=material,
        pawn_count=len(own_pawns),
        piece_counts=counts,
        pawns=_names(own_pawns),
        isolated_pawns=_names(isolated),
        doubled_pawns=_names(doubled),
        passed_pawns=_names(passed),
        backward_pawns=_names(backward),
        pawn_islands=islands,
        open_files=open_files,
        half_open_files=half_open,
        rooks_on_open_files=_names(rooks_on_open),
        king_square=chess.square_name(king) if king is not None else None,
        king_pawn_shield=shield,
        king_zone_attackers=zone_attackers,
        mobility=mobility,
        outposts=_names(outposts),
        bishop_pair=bishop_pair,
        space=space,
        center_control=center_control,
    )


def static_features(board: chess.Board) -> Features:
    return Features(
        white=_side_features(board, chess.WHITE),
        black=_side_features(board, chess.BLACK),
        fen=board.fen(),
    )


# ---------- comparison ----------


def _opponent(color: Color) -> Color:
    return "black" if color == "white" else "white"


def _files_of(squares: list[str]) -> str:
    return ",".join(sorted({sq[0] for sq in squares}))


def _doubled_extra(side: SideFeatures) -> int:
    """Pawns that are one too many on their file: three c-pawns count as two, so the number
    reads like the isolated and backward counts instead of counting files."""
    return len(side.doubled_pawns) - len({sq[0] for sq in side.doubled_pawns})


def _pawn_weaknesses(side: SideFeatures) -> list[str]:
    parts: list[str] = []
    if side.isolated_pawns:
        parts.append(f"고립 {_files_of(side.isolated_pawns)}폰 {len(side.isolated_pawns)}")
    doubled_files = sorted({sq[0] for sq in side.doubled_pawns})
    if doubled_files:
        # the count is extra pawns, the same unit as the isolated and backward counts next
        # to it and the same number _pawn_score subtracts
        parts.append(f"이중 {','.join(doubled_files)}폰 {_doubled_extra(side)}")
    if side.backward_pawns:
        parts.append(f"후진 {_files_of(side.backward_pawns)}폰 {len(side.backward_pawns)}")
    return parts


def _pawn_score(side: SideFeatures) -> float:
    weak = len(side.isolated_pawns) + _doubled_extra(side) + len(side.backward_pawns)
    return -weak - 0.5 * max(0, side.pawn_islands - 1)


def _pawn_text(side: SideFeatures) -> str:
    parts = _pawn_weaknesses(side) or ["약점 없음"]
    parts.append(f"폰 섬 {side.pawn_islands}")
    return ", ".join(parts)


def _activity_score(side: SideFeatures) -> float:
    return side.mobility + 2 * len(side.outposts)


def _activity_text(side: SideFeatures) -> str:
    text = f"활동성 {side.mobility}"
    if side.outposts:
        text += f", 아웃포스트 {' '.join(side.outposts)}"
    return text


def _king_score(side: SideFeatures) -> float:
    return side.king_pawn_shield - 2 * side.king_zone_attackers


def _king_text(side: SideFeatures) -> str:
    if side.king_square is None:
        return "킹 없음"
    return (
        f"킹 {side.king_square}, 폰 방패 {side.king_pawn_shield}, "
        f"킹존 공격 기물 {side.king_zone_attackers}"
    )


def _space_score(side: SideFeatures) -> float:
    return float(side.space)


def _space_text(side: SideFeatures) -> str:
    return f"{side.space}칸"


def _passed_score(side: SideFeatures) -> float:
    """One point per passed pawn plus a quarter point per rank it has advanced."""
    total = 0.0
    for name in side.passed_pawns:
        rank = int(name[1])
        rel = rank - 1 if side.color == "white" else 8 - rank
        total += 1 + 0.25 * max(0, rel - 1)
    return total


def _passed_text(side: SideFeatures) -> str:
    if not side.passed_pawns:
        return "통과폰 없음"
    return f"통과폰 {' '.join(side.passed_pawns)}"


def _files_score(side: SideFeatures) -> float:
    return len(side.half_open_files) + len(side.rooks_on_open_files)


def _files_text(side: SideFeatures) -> str:
    parts: list[str] = []
    if side.open_files:
        parts.append(f"열린 {','.join(side.open_files)}파일")
    if side.half_open_files:
        parts.append(f"반열림 {','.join(side.half_open_files)}파일")
    if not parts:
        parts.append("열린 파일 없음")
    if side.rooks_on_open_files:
        parts.append(f"룩/퀸 배치 {' '.join(side.rooks_on_open_files)}")
    return ", ".join(parts)


def _bishops_score(side: SideFeatures) -> float:
    return 1.0 if side.bishop_pair else 0.0


def _bishops_text(side: SideFeatures) -> str:
    return "있음" if side.bishop_pair else "없음"


def _material_score(side: SideFeatures) -> float:
    return float(side.material)


def _material_text(side: SideFeatures) -> str:
    return f"{side.material}점"


@dataclass(frozen=True)
class _Spec:
    label: str
    score: Callable[[SideFeatures], float]
    text: Callable[[SideFeatures], str]


SPECS: tuple[_Spec, ...] = (
    _Spec(LABEL_MATERIAL, _material_score, _material_text),
    _Spec(LABEL_PAWNS, _pawn_score, _pawn_text),
    _Spec(LABEL_ACTIVITY, _activity_score, _activity_text),
    _Spec(LABEL_KING, _king_score, _king_text),
    _Spec(LABEL_SPACE, _space_score, _space_text),
    _Spec(LABEL_PASSED, _passed_score, _passed_text),
    _Spec(LABEL_FILES, _files_score, _files_text),
    _Spec(LABEL_BISHOPS, _bishops_score, _bishops_text),
)


def feature_scores(features: Features, pov: Color) -> dict[str, float]:
    """Per-feature balance from `pov`'s point of view: own score minus the opponent's."""
    own = features.side(pov)
    opp = features.side(_opponent(pov))
    return {spec.label: spec.score(own) - spec.score(opp) for spec in SPECS}


def _pair_text(spec: _Spec, own: SideFeatures, opp: SideFeatures) -> str:
    return f"{spec.text(own)} (상대 {spec.text(opp)})"


def feature_diff(a: Features, b: Features, pov: Color) -> list[schemas.FeatureDiffRow]:
    """Compare two positions for `pov`. Each cell describes `pov`'s side in that position with
    the opponent in brackets; delta > 0 means `a` is better for `pov`."""
    rows: list[schemas.FeatureDiffRow] = []
    scores_a = feature_scores(a, pov)
    scores_b = feature_scores(b, pov)
    opp = _opponent(pov)
    for spec in SPECS:
        rows.append(
            schemas.FeatureDiffRow(
                feature=spec.label,
                a=_pair_text(spec, a.side(pov), a.side(opp)),
                b=_pair_text(spec, b.side(pov), b.side(opp)),
                delta=round(scores_a[spec.label] - scores_b[spec.label], 2),
            )
        )
    return rows


def summarize_features(features: Features, pov: Color) -> list[schemas.FeatureDiffRow]:
    """One position: column a is `pov`, column b the opponent; delta > 0 favours `pov`."""
    own = features.side(pov)
    opp = features.side(_opponent(pov))
    return [
        schemas.FeatureDiffRow(
            feature=spec.label,
            a=spec.text(own),
            b=spec.text(opp),
            delta=round(spec.score(own) - spec.score(opp), 2),
        )
        for spec in SPECS
    ]
