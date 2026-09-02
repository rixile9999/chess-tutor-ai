"""Layer 3 reasoning: divergence, feature comparison, alternatives, PV plans, counterfactuals.

Everything here is deterministic given the engine output it is handed; nothing calls an LLM.
Each Korean sentence is assembled only from the data returned next to it (rows, lines,
evaluations), so the verbalization layer can quote it and the verifier can check the facts.

Layer-2 modules from the concepts workstream (``chess_tutor.features``,
``chess_tutor.structure``) are imported lazily and every call degrades to empty rows or a
missing structure when they are absent or their signatures differ.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import chess

from chess_tutor.motifs import Motif, detect
from chess_tutor.schemas import (
    Classification,
    Color,
    Comparison,
    Counterfactual,
    EngineLine,
    FeatureDiffRow,
    Plan,
    PlanSketch,
    Score,
    StrategyView,
    StructureInfo,
    StructureSpan,
    YourMove,
)
from chess_tutor.services import plans as kb
from chess_tutor.values import PIECE_VALUE
from chess_tutor.verify import Claim

Analyse = Callable[[str, int, int], list[EngineLine]]
"""analyse(fen, depth, multipv) -> engine lines, injected so tests can fake the engine."""

HORIZON = 6
"""Plies of each PV played out before the two end positions are compared."""
COUNTERFACTUAL_DEPTH = 12
COUNTERFACTUAL_MULTIPV = 5
EVAL_KEEP = 0.3
"""A waiting move 'keeps the eval' when it stays within this many pawns of the best line."""
TIMING_MARGIN = 0.2
"""Smaller eval differences between 'now' and 'later' count as no timing difference."""

PIECE_KO: dict[chess.PieceType, str] = {
    chess.PAWN: "폰",
    chess.KNIGHT: "나이트",
    chess.BISHOP: "비숍",
    chess.ROOK: "룩",
    chess.QUEEN: "퀸",
    chess.KING: "킹",
}
COLOR_KO: dict[chess.Color, str] = {chess.WHITE: "백", chess.BLACK: "흑"}


# ---------- small helpers ----------


def _color(pov: Color | chess.Color) -> chess.Color:
    if isinstance(pov, str):
        return chess.WHITE if pov == "white" else chess.BLACK
    return bool(pov)


def _pawns(score: Score, pov: chess.Color) -> float:
    """Evaluation in pawns from ``pov``'s point of view."""
    value = score.as_pawns()
    return value if pov == chess.WHITE else -value


def _fmt(value: float) -> str:
    return f"{value:+.1f}"


def _has_batchim(text: str) -> tuple[bool, bool]:
    """(ends with a final consonant, that consonant is ㄹ) for Korean particle choice.

    Squares and SAN end in a digit: 0 1 3 6 7 8 carry a final consonant (영 일 삼 육 칠 팔),
    1 7 8 end in ㄹ."""
    text = text.rstrip("+#!?")
    if not text:
        return False, False
    last = text[-1]
    if last.isdigit():
        return last in "013678", last in "178"
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        return jong != 0, jong == 8
    return False, False


def _eul(text: str) -> str:
    return f"{text}{'을' if _has_batchim(text)[0] else '를'}"


def _ro(text: str) -> str:
    has, rieul = _has_batchim(text)
    return f"{text}{'로' if (not has or rieul) else '으로'}"


def _wa(text: str) -> str:
    return f"{text}{'과' if _has_batchim(text)[0] else '와'}"


def _ga(text: str) -> str:
    return f"{text}{'이' if _has_batchim(text)[0] else '가'}"


def _neun(text: str) -> str:
    return f"{text}{'은' if _has_batchim(text)[0] else '는'}"


def _play(board: chess.Board, moves: Iterable[chess.Move]) -> chess.Board:
    """Play moves on a copy, stopping silently at the first illegal one."""
    b = board.copy()
    for m in moves:
        if not b.is_legal(m):
            break
        b.push(m)
    return b


def _sans(board: chess.Board, moves: Iterable[chess.Move]) -> list[str]:
    b = board.copy()
    out: list[str] = []
    for m in moves:
        if not b.is_legal(m):
            break
        out.append(b.san(m))
        b.push(m)
    return out


def _line_moves(board: chess.Board, line: EngineLine) -> list[chess.Move]:
    """Moves of an engine line, from pv_uci when present else parsed from SAN."""
    moves: list[chess.Move] = []
    b = board.copy()
    tokens = line.pv_uci or line.pv
    for token in tokens:
        try:
            m = chess.Move.from_uci(token) if line.pv_uci else b.parse_san(token)
        except ValueError:
            break
        if not b.is_legal(m):
            break
        moves.append(m)
        b.push(m)
    return moves


def _move_number(board: chess.Board) -> str:
    n = board.fullmove_number
    return f"{n}." if board.turn == chess.WHITE else f"{n}..."


# ---------- pawn-structure facts used by the fallback feature rows ----------


def passed_pawns(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    """Pawns with no enemy pawn ahead on their own or adjacent files."""
    out: list[chess.Square] = []
    enemy = board.pieces(chess.PAWN, not color)
    for sq in board.pieces(chess.PAWN, color):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        ahead = range(r + 1, 8) if color == chess.WHITE else range(0, r)
        blocked = any(
            chess.square(ff, rr) in enemy
            for rr in ahead
            for ff in (f - 1, f, f + 1)
            if 0 <= ff <= 7
        )
        if not blocked:
            out.append(sq)
    return out


def isolated_pawns(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    own = board.pieces(chess.PAWN, color)
    files = {chess.square_file(sq) for sq in own}
    return [
        sq for sq in own if not ({chess.square_file(sq) - 1, chess.square_file(sq) + 1} & files)
    ]


def doubled_pawns(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    own = board.pieces(chess.PAWN, color)
    by_file: dict[int, list[chess.Square]] = {}
    for sq in own:
        by_file.setdefault(chess.square_file(sq), []).append(sq)
    return [sq for sqs in by_file.values() if len(sqs) > 1 for sq in sqs]


def material(board: chess.Board, color: chess.Color) -> int:
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        if piece_type == chess.KING:
            continue
        total += value * (
            len(board.pieces(piece_type, color)) - len(board.pieces(piece_type, not color))
        )
    return total


def mobility(board: chess.Board, color: chess.Color) -> int:
    b = board.copy(stack=False)
    if b.turn != color:
        b.push(chess.Move.null())
    return b.legal_moves.count()


def _names(squares: Iterable[chess.Square]) -> str:
    names = [chess.square_name(sq) for sq in sorted(squares)]
    return ", ".join(names) if names else "없음"


def _basic_rows(a: chess.Board, b: chess.Board, pov: chess.Color) -> list[FeatureDiffRow]:
    """Fallback feature rows computed here with python-chess when features.py is absent.

    Every row's a/b text is the literal fact (squares or counts) the delta was taken from."""
    opp = not pov
    me, you = COLOR_KO[pov], COLOR_KO[opp]

    def side_text(mine: Iterable[chess.Square], theirs: Iterable[chess.Square]) -> str:
        return f"{me} {_names(mine)} / {you} {_names(theirs)}"

    def material_text(board: chess.Board) -> str:
        diff = material(board, pov)
        if diff == 0:
            return "균형"
        return f"{me if diff > 0 else you} +{abs(diff)}"

    rows: list[FeatureDiffRow] = []
    rows.append(
        FeatureDiffRow(
            feature="물질",
            a=material_text(a),
            b=material_text(b),
            delta=float(material(a, pov) - material(b, pov)),
        )
    )
    for label, fn, sign in (
        ("통과폰", passed_pawns, 1),
        ("고립폰", isolated_pawns, -1),
        ("겹폰", doubled_pawns, -1),
    ):
        mine_a, theirs_a = fn(a, pov), fn(a, opp)
        mine_b, theirs_b = fn(b, pov), fn(b, opp)
        score_a = sign * (len(mine_a) - len(theirs_a))
        score_b = sign * (len(mine_b) - len(theirs_b))
        rows.append(
            FeatureDiffRow(
                feature=label,
                a=side_text(mine_a, theirs_a),
                b=side_text(mine_b, theirs_b),
                delta=float(score_a - score_b),
            )
        )
    mob_a = mobility(a, pov) - mobility(a, opp)
    mob_b = mobility(b, pov) - mobility(b, opp)
    rows.append(
        FeatureDiffRow(
            feature="기물 활동",
            a=f"{me} {mobility(a, pov)}수 / {you} {mobility(a, opp)}수",
            b=f"{me} {mobility(b, pov)}수 / {you} {mobility(b, opp)}수",
            delta=round((mob_a - mob_b) / 10.0, 2),
        )
    )
    return rows


def _coerce_rows(value: object) -> list[FeatureDiffRow]:
    rows: list[FeatureDiffRow] = []
    if not isinstance(value, list | tuple):
        return rows
    for item in value:
        try:
            if isinstance(item, FeatureDiffRow):
                rows.append(item)
            elif isinstance(item, dict):
                rows.append(FeatureDiffRow.model_validate(item))
            elif hasattr(item, "model_dump"):
                rows.append(FeatureDiffRow.model_validate(item.model_dump()))
        except ValueError:
            continue
    return rows


def _feature_rows(a: chess.Board, b: chess.Board, pov: chess.Color) -> list[FeatureDiffRow]:
    """features.feature_diff (concepts workstream) when importable, else the fallback rows."""
    try:
        from chess_tutor import features
    except ImportError:
        return _basic_rows(a, b, pov)
    try:
        rows = features.feature_diff(
            features.static_features(a), features.static_features(b), kb.side_of(pov)
        )
    except (TypeError, ValueError, AttributeError, KeyError):
        return _basic_rows(a, b, pov)
    return _coerce_rows(rows) or _basic_rows(a, b, pov)


def _differs(row: FeatureDiffRow) -> bool:
    if row.delta is not None:
        return abs(row.delta) > 1e-9
    return row.a != row.b


# ---------- divergence and comparison ----------


def divergence(
    board: chess.Board, pv_a: list[chess.Move], pv_b: list[chess.Move]
) -> tuple[int | None, str | None]:
    """First ply index where the two lines differ and the FEN reached in line a right after
    that move. (None, None) when the lines are identical. When one line is a prefix of the
    other the index is the shorter length and the FEN is the end of line a's common part."""
    n = min(len(pv_a), len(pv_b))
    idx = next((i for i in range(n) if pv_a[i] != pv_b[i]), None)
    if idx is None:
        if len(pv_a) == len(pv_b):
            return None, None
        idx = n
    return idx, _play(board, pv_a[: idx + 1]).fen()


def _favours(row: FeatureDiffRow, a_san: str, b_san: str) -> str:
    return a_san if (row.delta or 0.0) > 0 else b_san


def score_text(score: Score) -> str:
    """'+0.4', '-2.1' or '#3', always from White's point of view (the eval bar convention)."""
    if score.mate is not None:
        return f"#{score.mate}"
    return f"{(score.cp or 0) / 100:+.1f}"


def _verdict_clause(a_san: str, b_san: str, eval_a: Score, eval_b: Score) -> str:
    return f"엔진 평가는 {a_san} {score_text(eval_a)}, {b_san} {score_text(eval_b)}입니다."


def _mate_for(score: Score, pov: chess.Color) -> int:
    """+1 when the score is a forced mate for ``pov``, -1 when it is one against, else 0."""
    if score.mate is None:
        return 0
    return 1 if (score.mate > 0) == (pov == chess.WHITE) else -1


def _comparison_summary(
    a_san: str,
    b_san: str,
    rows: list[FeatureDiffRow],
    eval_a: Score,
    eval_b: Score,
    pov: chess.Color = chess.WHITE,
) -> str:
    """One sentence on why a beats b: the features that speak for ``a_san`` and then the
    engine verdict.

    The heading above this sentence is '왜 a가 b보다 나은가', so the leading clause may never
    be built from a row that favours b. When every row favours b the sentence concedes that
    much and ends on the evaluation, which is the fact that decides the comparison."""
    verdict = _verdict_clause(a_san, b_san, eval_a, eval_b)
    if _mate_for(eval_a, pov) == 1:
        return f"{_ga(a_san)} 메이트로 이어집니다. {verdict}"
    if _mate_for(eval_b, pov) == -1:
        return f"{_ga(b_san)} 메이트를 허용합니다. {verdict}"
    if eval_a.mate is not None or eval_b.mate is not None:
        return verdict
    ranked = sorted(
        (r for r in rows if r.delta is not None), key=lambda r: abs(r.delta or 0.0), reverse=True
    )
    ours = [r for r in ranked if _favours(r, a_san, b_san) == a_san][:2]
    if ours:
        features = (
            _wa(ours[0].feature) + " " + ours[1].feature if len(ours) == 2 else ours[0].feature
        )
        return f"{_ga(a_san)} {features}에서 앞섭니다. {verdict}"
    theirs = [r for r in ranked if _favours(r, a_san, b_san) == b_san][:1]
    if theirs:
        return f"{theirs[0].feature}만 놓고 보면 {_ga(b_san)} 앞서지만, {verdict}"
    return f"두 수 뒤 국면의 특징 차이는 찾지 못했습니다. {verdict}"


def compare_moves(
    board: chess.Board,
    a_san: str,
    b_san: str,
    pv_a: list[chess.Move],
    pv_b: list[chess.Move],
    pov: Color | chess.Color,
    eval_a: Score,
    eval_b: Score,
    horizon: int = HORIZON,
) -> Comparison:
    """Why a is better than b: play both lines to the same depth, diff the end positions for
    pov, keep only rows that differ and summarise them in one sentence.

    ``pv_a``/``pv_b`` start with the compared moves themselves. Both lines stop at the same
    ply so the two positions are comparable at all (a quiet position six plies later says
    nothing about a position three plies in), and when either line ends in mate the feature
    rows are dropped: counting material in a mating position is meaningless and the
    evaluation already carries the whole answer."""
    color = _color(pov)
    plies = min(len(pv_a), len(pv_b), horizon)
    end_a = _play(board, pv_a[:plies])
    end_b = _play(board, pv_b[:plies])
    div_ply, div_fen = divergence(board, pv_a, pv_b)
    decided = (
        end_a.is_game_over()
        or end_b.is_game_over()
        or eval_a.mate is not None
        or eval_b.mate is not None
    )
    rows = [] if decided else [r for r in _feature_rows(end_a, end_b, color) if _differs(r)]
    return Comparison(
        a_san=a_san,
        b_san=b_san,
        divergence_ply=div_ply,
        divergence_fen=div_fen,
        rows=rows,
        summary=_comparison_summary(a_san, b_san, rows, eval_a, eval_b, color),
    )


# ---------- alternatives ----------


def _valuable_attacked(board: chess.Board, from_sq: chess.Square, color: chess.Color) -> list[str]:
    """Squares of ``color``'s minor-or-better pieces attacked from ``from_sq``."""
    return [
        chess.square_name(sq)
        for sq in sorted(board.attacks(from_sq) & board.occupied_co[color])
        if PIECE_VALUE[board.piece_type_at(sq) or chess.PAWN] >= 3
    ]


def _named_targets(
    board: chess.Board, squares: Iterable[chess.Square], limit: int = 2
) -> list[tuple[chess.Square, str]]:
    """The ``limit`` most valuable of ``squares``, each as '(square, "d7 퀸")'.

    Prose names pieces, not square lists, and the caller claims exactly these squares, so the
    text and the claims stay in lockstep however many targets the motif found."""
    named = [
        (PIECE_VALUE[piece], sq, f"{chess.square_name(sq)} {PIECE_KO[piece]}")
        for sq in squares
        if (piece := board.piece_type_at(sq)) is not None
    ]
    named.sort(key=lambda item: (-item[0], item[1]))
    return [(sq, text) for _, sq, text in named[:limit]]


def _join_ko(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return _wa(items[0]) + " " + _join_ko(items[1:])


def _describe_move(board: chess.Board, move: chess.Move) -> str:
    """One clause for a move on this board, e.g. 'd5 나이트를 나이트로 잡습니다'."""
    piece = board.piece_at(move.from_square)
    if piece is None:
        return board.san(move)
    to_name = chess.square_name(move.to_square)
    mover = PIECE_KO[piece.piece_type]
    if board.is_castling(move):
        side = "킹사이드" if board.is_kingside_castling(move) else "퀸사이드"
        text = f"{side} 캐슬링을 합니다"
    elif board.is_en_passant(move):
        text = f"{to_name}에서 앙파상으로 폰을 잡습니다"
    elif board.is_capture(move):
        captured = board.piece_type_at(move.to_square) or chess.PAWN
        text = f"{_eul(to_name + ' ' + PIECE_KO[captured])} {_ro(mover)} 잡습니다"
    else:
        text = f"{_eul(mover)} {_ro(to_name)} 옮깁니다"
    if move.promotion:
        text += f" (승격 {PIECE_KO[move.promotion]})"
    if board.gives_check(move):
        text += ". 체크입니다"
    return text


SPOKEN_MOTIFS = ("fork", "discovered_attack")
"""Motifs an alternative's prose names. The others (mate threats, pins, trapped pieces) need
facts this clause cannot state or check, and a wrong clause is worse than a missing one."""


def _motif_clause(
    board_after: chess.Board, motif: Motif
) -> tuple[str, chess.Square, list[chess.Square]]:
    """The motif in one clause plus (attacking square, named target squares) for the claims."""
    if motif.kind == "fork":
        source = motif.mover
        piece = board_after.piece_type_at(source) or chess.PAWN
        head = f"{chess.square_name(source)} {PIECE_KO[piece]}"
        # A checking fork already says '체크와 함께', so the king is not named again.
        squares = [t for t in motif.targets if board_after.piece_type_at(t) != chess.KING] or list(
            motif.targets
        )
        named = _named_targets(board_after, squares)
        tail = "체크와 함께 " if motif.with_check else ""
        verb = "함께 노립니다" if len(named) > 1 else "노립니다"
        text = f"{_ga(head)} {tail}{_eul(_join_ko([n for _, n in named]))} {verb}"
    else:
        source = motif.attacker
        piece = board_after.piece_type_at(source) or chess.QUEEN
        head = f"{chess.square_name(source)} {PIECE_KO[piece]}"
        named = _named_targets(board_after, motif.targets, limit=1)
        text = f"{head}의 길이 열려 {_eul(_join_ko([n for _, n in named]))} 겨냥합니다"
    return text, source, [sq for sq, _ in named]


def _exchange_on(
    board: chess.Board, moves: list[chess.Move], square: chess.Square
) -> tuple[list[str], chess.Board]:
    """The run of captures on ``square`` at the head of ``moves`` (the exchange the move just
    started), as SAN, and the position once they are done."""
    probe = board.copy()
    sans: list[str] = []
    for move in moves:
        if not probe.is_legal(move) or not probe.is_capture(move) or move.to_square != square:
            break
        sans.append(probe.san(move))
        probe.push(move)
    return sans, probe


def explain_alternative(
    board: chess.Board,
    san: str,
    pv: list[chess.Move],
    eval: Score,
    best_eval: Score,
    pov: Color | chess.Color,
) -> tuple[str, list[Claim]]:
    """Short Korean 'why' for an alternative, with the board facts it states as claims.

    A coach says what the move does and what it costs, so this is at most three sentences:
    what the move achieves, how the exchange it starts settles, and (only when it is not the
    engine's move) what it gives up against the best line. The opponent's recapture is never
    narrated on its own: '백은 Qxb4: b4 퀸을 퀸으로 잡습니다' ends the paragraph on the queen
    being taken and reads as if the recommended move hung it."""
    color = _color(pov)
    if not pv:
        pv = [board.parse_san(san)]
    first = pv[0]
    if not board.is_legal(first):
        raise ValueError(f"illegal move {first} in {board.fen()}")
    mover = board.turn
    fen = board.fen()
    to_name = chess.square_name(first.to_square)
    parts: list[str] = []
    claims: list[Claim] = [Claim(kind="legal_move", fen=fen, object=board.san(first))]

    after = board.copy()
    after.push(first)
    if board.gives_check(first):
        claims.append(Claim(kind="is_check", fen=after.fen()))
    clause = _describe_move(board, first)
    if board.is_en_passant(first):
        claims.append(Claim(kind="square_empty", fen=fen, subject=to_name))
    elif board.is_capture(first):
        victim = board.piece_at(first.to_square)
        if victim is not None:
            claims.append(Claim(kind="piece_on", fen=fen, subject=to_name, object=victim.symbol()))
        captured = PIECE_KO[board.piece_type_at(first.to_square) or chess.PAWN]
        # The captured piece was also eyeing the piece that just took it, which is no news.
        removed = _named_targets(
            board,
            [
                square
                for name in _valuable_attacked(board, first.to_square, mover)
                if (square := chess.parse_square(name)) != first.from_square
            ],
        )
        if removed:
            for square, _ in removed:
                claims.append(
                    Claim(
                        kind="attacks",
                        fen=fen,
                        subject=to_name,
                        object=chess.square_name(square),
                    )
                )
            threats = _join_ko([name for _, name in removed])
            clause += f". {to_name} {captured}가 {_eul(threats)} 노리던 위협이 사라집니다"
    for motif in detect(board, first):
        if motif.kind not in SPOKEN_MOTIFS:
            continue
        text, source, targets = _motif_clause(after, motif)
        if not targets:
            continue
        clause += f". {text}"
        for target in targets:
            claims.append(
                Claim(
                    kind="attacks",
                    fen=after.fen(),
                    subject=chess.square_name(source),
                    object=chess.square_name(target),
                )
            )
    parts.append(clause)

    sans, settled = _exchange_on(after, pv[1:], first.to_square)
    if sans:
        probe = after.copy()
        for reply_san in sans:
            claims.append(Claim(kind="legal_move", fen=probe.fen(), object=reply_san))
            probe.push_san(reply_san)
        opponent = COLOR_KO[not mover]
        if len(sans) >= 2:
            trade = (
                f"{opponent}이 {_ro(sans[0])} 받아도 {COLOR_KO[mover]}이 {_ro(sans[1])} 되잡습니다"
            )
        else:
            trade = f"{opponent}이 {_ro(sans[0])} 되잡습니다"
        net = material(settled, mover) - material(board, mover)
        if net > 0:
            trade += f". 교환 뒤 {COLOR_KO[mover]}이 폰 {net}개만큼 앞섭니다"
        elif net < 0:
            trade += f". 교환 뒤 {COLOR_KO[not mover]}이 폰 {-net}개만큼 앞섭니다"
        else:
            trade += ". 교환은 대등합니다"
        parts.append(trade)

    if _mate_for(best_eval, color) == 1 and _mate_for(eval, color) != 1:
        # ±100 pawns stands for any mate, so a pawn difference here would be fiction.
        parts.append("메이트를 놓칩니다")
    return ". ".join(parts) + ".", claims


# ---------- plan extraction from a PV ----------


def _is_break(board: chess.Board, move: chess.Move) -> bool:
    """A pawn advance that, once played, attacks an enemy pawn or runs into one."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type != chess.PAWN or board.is_capture(move):
        return False
    after = board.copy(stack=False)
    after.push(move)
    enemy_pawns = after.pieces(chess.PAWN, not piece.color)
    if after.attacks(move.to_square) & enemy_pawns:
        return True
    step = 8 if piece.color == chess.WHITE else -8
    front = move.to_square + step
    return 0 <= front < 64 and front in enemy_pawns


@dataclass
class _Pending:
    square: chess.Square
    san: str
    capturer: chess.PieceType
    captured: chess.PieceType
    by_side: bool


def extract_plan(board: chess.Board, pv: list[chess.Move]) -> PlanSketch:
    """What the side to move does in this line: where its pieces end up, which pawn breaks it
    plays, which exchanges happen and where the king goes."""
    side = board.turn
    b = board.copy()
    destinations: dict[str, str] = {}
    tracked: dict[chess.Square, str] = {}
    pawn_breaks: list[str] = []
    exchanges: list[str] = []
    exchange_pieces: list[tuple[chess.PieceType, chess.PieceType]] = []
    king_moves: list[str] = []
    pending: _Pending | None = None
    plies = 0

    for move in pv:
        if not b.is_legal(move):
            break
        piece = b.piece_at(move.from_square)
        assert piece is not None
        san = b.san(move)
        captured = b.piece_type_at(move.to_square)
        if b.is_en_passant(move):
            captured = chess.PAWN
        by_side = piece.color == side

        if by_side:
            if piece.piece_type == chess.KING:
                king_moves.append(san)
            elif piece.piece_type == chess.PAWN:
                if _is_break(b, move):
                    pawn_breaks.append(san)
            else:
                key = tracked.pop(
                    move.from_square,
                    f"{piece.symbol().upper()}{chess.square_name(move.from_square)}",
                )
                tracked[move.to_square] = key
                destinations[key] = chess.square_name(move.to_square)
        if captured is not None:
            tracked.pop(move.to_square, None)
            if pending is not None and pending.square == move.to_square:
                exchanges.append(f"{pending.san} {san}")
                gave, got = (
                    (pending.capturer, pending.captured)
                    if pending.by_side
                    else (pending.captured, pending.capturer)
                )
                exchange_pieces.append((gave, got))
                pending = None
            else:
                pending = _Pending(move.to_square, san, piece.piece_type, captured, by_side)
        else:
            pending = None
        b.push(move)
        plies += 1

    parts: list[str] = []
    for key, sq in destinations.items():
        piece_type = chess.Piece.from_symbol(key[0]).piece_type
        parts.append(f"{_eul(PIECE_KO[piece_type])} {_ro(sq)}")
    dots = "..." if side == chess.BLACK else ""
    parts.extend(f"{dots}{san} 브레이크" for san in pawn_breaks)
    for gave, got in exchange_pieces:
        if gave == got:
            parts.append(f"{PIECE_KO[gave]} 교환")
        else:
            parts.append(f"{_wa(PIECE_KO[gave])} {PIECE_KO[got]} 교환")
    for san in king_moves:
        if san.startswith("O-O"):
            parts.append("캐슬링")
        else:
            parts.append(f"킹을 {_ro(san.lstrip('K').rstrip('+#'))}")
    summary = (
        ", ".join(parts) if parts else "이 수순에서는 뚜렷한 기물 재배치나 브레이크가 없습니다"
    )
    return PlanSketch(
        side=kb.side_of(side),
        piece_destinations=destinations,
        pawn_breaks=pawn_breaks,
        exchanges=exchanges,
        king_moves=king_moves,
        plies=plies,
        summary=summary,
    )


# ---------- counterfactual (timing) ----------


def _quietness(board: chess.Board, move: chess.Move) -> tuple[int, int, int]:
    """Lower is quieter: (captures/checks, enemy pieces newly attacked, not a king/rook move)."""
    noisy = int(board.is_capture(move) or board.gives_check(move) or bool(move.promotion))
    after = board.copy(stack=False)
    after.push(move)
    attacked = len(after.attacks(move.to_square) & after.occupied_co[after.turn])
    piece = board.piece_type_at(move.from_square)
    shuffle = 0 if piece in (chess.KING, chess.ROOK) else 1
    return noisy, attacked, shuffle


def _waiting_move(
    board: chess.Board, target: chess.Move, analyse: Analyse, depth: int, multipv: int
) -> chess.Move | None:
    """Quietest legal non-pawn move that keeps the evaluation, else a king/rook shuffle."""
    pov = board.turn
    lines = analyse(board.fen(), depth, multipv)
    best = max((_pawns(ln.score, pov) for ln in lines), default=None)
    candidates: list[tuple[tuple[int, int, int], int, chess.Move]] = []
    for line in lines:
        moves = _line_moves(board, line)
        if not moves or moves[0] == target:
            continue
        move = moves[0]
        if board.piece_type_at(move.from_square) == chess.PAWN:
            continue
        if best is not None and _pawns(line.score, pov) < best - EVAL_KEEP:
            continue
        candidates.append((_quietness(board, move), line.rank, move))
    if candidates:
        return min(candidates)[2]
    fallback = [
        (_quietness(board, m), m.uci(), m)
        for m in board.legal_moves
        if m != target
        and board.piece_type_at(m.from_square) != chess.PAWN
        and not board.is_capture(m)
        and not board.gives_check(m)
    ]
    if not fallback:
        return None
    move = min(fallback)[2]
    if best is None:
        return move
    # The fallback never went through the engine, so check it keeps the evaluation too:
    # a shuffle that drops a piece would turn the timing question into a blunder report.
    probe = board.copy()
    probe.push(move)
    lines = analyse(probe.fen(), depth, 1)
    if lines and _pawns(lines[0].score, pov) < best - EVAL_KEEP:
        return None
    return move


def _timing_mate_verdict(mate_now: int, mate_later: int, delayed: str) -> str:
    """Timing verdict when a forced mate is on one side of the comparison, said in words."""
    if mate_now == mate_later:
        return f"시점 차이 없음: 미뤄도 결과가 같습니다 ({delayed})"
    if mate_now == 1:
        return f"지금이 적기: 미루면 메이트를 놓칩니다 ({delayed})"
    if mate_later == 1:
        return f"아직 이르다: 준비 후에 메이트가 생깁니다 ({delayed})"
    if mate_now == -1:
        return f"아직 이르다: 지금 두면 메이트를 당합니다 ({delayed})"
    return f"지금이 적기: 미루면 메이트를 당합니다 ({delayed})"


def counterfactual(
    board: chess.Board,
    target_san: str,
    analyse: Analyse,
    depth: int = COUNTERFACTUAL_DEPTH,
    multipv: int = COUNTERFACTUAL_MULTIPV,
) -> Counterfactual:
    """Is now the moment for ``target_san``? Compare playing it immediately with playing a
    waiting move first, letting the opponent reply, and playing it one move later."""
    pov = board.turn
    target = board.parse_san(target_san)
    question = f"지금 {_eul(_move_number(board) + target_san)} 두면?"

    now = board.copy()
    now.push(target)
    now_line = _first_line(analyse, now.fen(), depth)
    eval_now = now_line.score
    line = [target_san, *_sans(now, _line_moves(now, now_line))][:8]

    waiting = _waiting_move(board, target, analyse, depth, multipv)
    if waiting is None:
        return Counterfactual(
            question=question,
            line=line,
            verdict=f"비교할 대기수가 없습니다. 지금 두면 {_fmt(_pawns(eval_now, pov))}",
            eval=eval_now,
        )
    later = board.copy()
    later.push(waiting)
    reply_line = _first_line(analyse, later.fen(), depth)
    reply_moves = _line_moves(later, reply_line)
    delayed_sans = [board.san(waiting)]
    if reply_moves:
        delayed_sans.append(later.san(reply_moves[0]))
        later.push(reply_moves[0])
    target_later = next((m for m in later.legal_moves if _same_move(later, m, board, target)), None)
    if target_later is None:
        eval_later = reply_line.score
        blocked = True
    else:
        delayed_sans.append(later.san(target_later))
        later.push(target_later)
        eval_later = _first_line(analyse, later.fen(), depth).score
        blocked = False

    diff = _pawns(eval_later, pov) - _pawns(eval_now, pov)
    delayed = " ".join(delayed_sans)
    mate_now, mate_later = _mate_for(eval_now, pov), _mate_for(eval_later, pov)
    if blocked:
        verdict = f"지금이 적기: {delayed} 뒤에는 {_eul(target_san)} 둘 수 없습니다"
    elif mate_now or mate_later:
        # Score.as_pawns() reports any mate as ±100, so a pawn difference between a mate and
        # an ordinary evaluation would be a made-up number ('미루면 -105.9').
        verdict = _timing_mate_verdict(mate_now, mate_later, delayed)
    elif diff < -TIMING_MARGIN:
        verdict = f"지금이 적기: 미루면 {_fmt(diff)} ({delayed})"
    elif diff > TIMING_MARGIN:
        verdict = f"아직 이르다: 준비 후가 {_fmt(diff)} 낫다 ({delayed})"
    else:
        verdict = f"시점 차이 없음: 미뤄도 {_fmt(diff)} ({delayed})"
    return Counterfactual(question=question, line=line, verdict=verdict, eval=eval_now)


def _same_move(
    board: chess.Board, move: chess.Move, ref_board: chess.Board, ref: chess.Move
) -> bool:
    """Same piece type from the same origin to the same destination."""
    return (
        move.from_square == ref.from_square
        and move.to_square == ref.to_square
        and board.piece_type_at(move.from_square) == ref_board.piece_type_at(ref.from_square)
    )


def _first_line(analyse: Analyse, fen: str, depth: int) -> EngineLine:
    lines = analyse(fen, depth, 1)
    if not lines:
        raise ValueError(f"engine returned no line for {fen}")
    return lines[0]


# ---------- strategy view ----------


def _moves_between(boards: list[chess.Board], upto: int) -> list[chess.Move]:
    """Moves that turn boards[i] into boards[i+1] for i < upto."""
    moves: list[chess.Move] = []
    for i in range(min(upto, len(boards) - 1)):
        before, after = boards[i], boards[i + 1]
        if after.move_stack and len(after.move_stack) == len(before.move_stack) + 1:
            moves.append(after.move_stack[-1])
            continue
        found = next(
            (m for m in before.legal_moves if _play(before, [m]).epd() == after.epd()), None
        )
        if found is None:
            break
        moves.append(found)
    return moves


def _classify(board: chess.Board) -> StructureInfo | None:
    try:
        from chess_tutor import structure
    except ImportError:
        return None
    try:
        info = structure.classify(board)
    except Exception:  # noqa: BLE001 - layer 2 must never break the view
        return None
    if info is None:
        return None
    if isinstance(info, StructureInfo):
        return info
    try:
        return StructureInfo.model_validate(info if isinstance(info, dict) else info.model_dump())
    except (ValueError, AttributeError):
        return None


def _timeline(boards: list[chess.Board]) -> list[StructureSpan]:
    try:
        from chess_tutor import structure
    except ImportError:
        return []
    try:
        spans = structure.timeline(boards)
    except Exception:  # noqa: BLE001
        return []
    out: list[StructureSpan] = []
    for span in spans or []:
        try:
            if isinstance(span, StructureSpan):
                out.append(span)
            else:
                out.append(
                    StructureSpan.model_validate(
                        span if isinstance(span, dict) else span.model_dump()
                    )
                )
        except (ValueError, AttributeError):
            continue
    return out


def _summarize_features(board: chess.Board, pov: chess.Color) -> list[FeatureDiffRow]:
    try:
        from chess_tutor import features
    except ImportError:
        return []
    try:
        rows = features.summarize_features(features.static_features(board), kb.side_of(pov))
    except (TypeError, ValueError, AttributeError, KeyError):
        return []
    return _coerce_rows(rows)


def _hint_plan(plans: list[Plan], board: chess.Board, move: chess.Move) -> Plan | None:
    """First plan with a hint step equal to this move (piece type + destination)."""
    for plan in plans:
        if any(kb.hint_matches_move(h, board, move) for h in plan.moves_hint):
            return plan
    return None


def _pv_plan(
    plans: list[Plan], board: chess.Board, pvs: list[list[chess.Move]], played: chess.Move
) -> Plan | None:
    """A pv_match plan whose matching line contains the played move (as the mover's move)."""
    mover = board.turn
    for pv in pvs:
        if played not in pv[::2]:
            continue
        steps = [(b, m) for b, m in kb.walk(board, pv) if b.turn == mover]
        for plan in plans:
            if plan.status != "pv_match":
                continue
            if any(kb.hint_matches_move(h, b, m) for h in plan.moves_hint for b, m in steps):
                return plan
    return None


def strategy_view(
    boards: list[chess.Board],
    ply: int,
    lines_before: list[EngineLine],
    played_san: str,
    classification: Classification,
    pov: Color | chess.Color,
    analyse: Analyse | None = None,
    record: dict[str, float | int | None] | None = None,
    *,
    structure: StructureInfo | None = None,
    depth: int = COUNTERFACTUAL_DEPTH,
) -> StrategyView:
    """Strategy tab for the move played from boards[ply].

    ``boards[i]`` is the position before ply ``i`` (boards[0] is the start). ``structure``
    overrides the classifier when the caller already has it; otherwise
    ``chess_tutor.structure.classify`` is tried and the view degrades to no structure."""
    if not 0 <= ply < len(boards):
        raise ValueError(f"ply {ply} outside boards[0..{len(boards) - 1}]")
    board = boards[ply]
    color = _color(pov)
    mover = board.turn
    info = structure or _classify(board)
    timeline = _timeline(boards)
    played_moves = _moves_between(boards, ply)
    pvs = [_line_moves(board, ln) for ln in lines_before]
    pvs = [pv for pv in pvs if pv]

    plans: list[Plan] = []
    if info is not None:
        mirror = kb.mirrored(info.key, board)
        for side in (kb.side_of(color), kb.side_of(not color)):
            plans.extend(
                kb.match_plans(
                    info.key,
                    side,
                    pvs,
                    board,
                    played_moves,
                    mirror=mirror,
                    start_board=boards[0],
                )
            )

    your_move: YourMove | None = None
    try:
        played = board.parse_san(played_san)
    except ValueError:
        played = None
    if played is not None:
        mover_plans = [p for p in plans if p.side == kb.side_of(mover)]
        hint_plan = _hint_plan(mover_plans, board, played)
        pv_plan = None if hint_plan else _pv_plan(mover_plans, board, pvs, played)
        label = f"{_move_number(board)}{played_san}"
        best_san = lines_before[0].pv[0] if lines_before and lines_before[0].pv else None
        if hint_plan is not None:
            note = f"{_neun(label)} 계획 '{hint_plan.title}'의 수입니다"
            if best_san == played_san:
                note += ". 엔진 1순위 수와 같습니다"
        elif pv_plan is not None:
            note = (
                f"{_neun(label)} 엔진 수순 안에 있고, 그 수순은 계획 '{pv_plan.title}'로 이어집니다"
            )
        elif not mover_plans:
            note = f"{label}: 이 국면의 구조에는 등록된 계획이 없습니다"
        else:
            note = f"{_neun(label)} 이 구조의 계획 목록에 없는 수입니다"
            if best_san and best_san != played_san:
                try:
                    best_move = board.parse_san(best_san)
                except ValueError:
                    best_move = None
                best_plan = _hint_plan(mover_plans, board, best_move) if best_move else None
                if best_plan is not None:
                    note += f". 엔진 최선 {_neun(best_san)} 계획 '{best_plan.title}'의 수입니다"
        your_move = YourMove(
            san=played_san,
            classification=classification,
            plan_match=hint_plan is not None or pv_plan is not None,
            note=note,
        )

    cf: Counterfactual | None = None
    if analyse is not None:
        cf = _top_break_counterfactual(board, plans, analyse, depth)

    return StrategyView(
        structure=info,
        timeline=timeline,
        plans=plans,
        your_move=your_move,
        counterfactual=cf,
        features=_summarize_features(board, color),
        record=dict(record or {}),
    )


def _top_break_counterfactual(
    board: chess.Board, plans: list[Plan], analyse: Analyse, depth: int
) -> Counterfactual | None:
    """Counterfactual for the first break move (a pawn hint legal now) among the plans of the
    side to move, preferring plans the engine lines already contain."""
    side = kb.side_of(board.turn)
    ordered = sorted(
        (p for p in plans if p.side == side and p.status in ("pv_match", "later")),
        key=lambda p: p.status != "pv_match",
    )
    for plan in ordered:
        for hint in kb.break_hints(plan):
            step = kb.parse_hint(hint)[0]
            move = next((m for m in board.legal_moves if step.matches(board, m)), None)
            if move is None:
                continue
            try:
                return counterfactual(board, board.san(move), analyse, depth=depth)
            except (ValueError, IndexError):
                return None
    return None
