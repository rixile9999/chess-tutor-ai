"""Layer 4: turn review facts into Korean prose the verifier can check.

Two writers produce an :class:`Explanation` from the same :class:`ReviewFacts`:

* :func:`template_explanation` is deterministic. Every sentence is assembled from the facts
  (motifs, lines, evaluations, the human view) and carries the board facts it used as
  :class:`Claim` objects.
* :func:`llm_explanation` asks Claude to phrase the same facts. The model never judges the
  position: it receives the facts JSON and returns sentences that each list the claims they
  rely on. It runs only when an API key is configured and returns None on any failure.

Both go through the same gate: :func:`verify_all` checks every claim on its board and a
sentence with a failing claim is dropped. :func:`explain` picks the LLM text when it survived
the gate with at least two sentences and the template otherwise.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal

import chess
from pydantic import BaseModel

from chess_tutor.config import get_settings
from chess_tutor.motifs import PIECE_KOREAN
from chess_tutor.schemas import (
    Alternative,
    Classification,
    Color,
    Comparison,
    Explanation,
    HumanView,
    MotifOut,
    Refutation,
    Score,
)
from chess_tutor.verify import Claim, ClaimKind, Verdict, verify

log = logging.getLogger(__name__)

MIN_LLM_SENTENCES = 2
"""The LLM text replaces the template only when at least this many sentences survived."""
LLM_MAX_TOKENS = 2048
LLM_TIMEOUT = 45.0

CLASS_KO: dict[str, str] = {
    "book": "책",
    "best": "최선",
    "good": "좋음",
    "inaccuracy": "부정확",
    "mistake": "실수",
    "blunder": "블런더",
    "forced": "강제",
}
COLOR_KO: dict[str, str] = {"white": "백", "black": "흑"}
SOURCE_KO: dict[str, str] = {"maia": "Maia 예측", "engine": "엔진 기반 추정"}

_SQUARE_MENTION = re.compile(r"[a-h][1-8]")
"""A square name anywhere in a sentence (no word boundaries: Korean particles and piece
letters sit right next to it, as in 'Rd1이')."""
_QUOTED = re.compile(r"'[^']*'")
"""Quoted plan titles from the knowledge base ('...d5 브레이크'): names, not board claims."""
_SAN_PREFIX = re.compile(r"^[^:]{1,12}:\s*")


# ---------- facts ----------


class ReviewFacts(BaseModel):
    """Everything the verbalizer needs about one move. Built by services.review.build_facts;
    the LLM receives exactly this as JSON."""

    game_id: int
    ply: int
    san: str
    uci: str
    color: Color
    move_label: str
    """'20… Qd7' or '21. Nxf6+' (move number plus SAN)."""
    fen_before: str
    fen_after: str
    classification: Classification
    eval_before: Score
    eval_after: Score
    best_san: str | None = None
    natural_reason: str | None = None
    """What the played move does, from services.maia.natural_reason."""
    natural_claims: list[Claim] = []
    played_motifs: list[MotifOut] = []
    """Motifs the played move itself creates."""
    refutation: Refutation | None = None
    punish_label: str | None = None
    """Move label of the punishing move, e.g. '21. Nxf6+'."""
    fen_punished: str | None = None
    """Position after the punishing move."""
    note_line: list[str] = []
    """[X, Z] behind refutation.note: X the second-best punishment, Z the reply that meets it."""
    alternatives: list[Alternative] = []
    comparison: Comparison | None = None
    human: HumanView | None = None
    strategy_note: str | None = None
    structure_name: str | None = None
    positions: dict[str, str] = {}
    """Named FENs ('before', 'after', 'punished', ...) that LLM claims may reference."""


@dataclass
class Sentence:
    text: str
    claims: list[Claim] = field(default_factory=list)
    unresolved: int = 0
    """Claims that could not be mapped to a board. They count as failed, so such a sentence
    never passes. Squares the claims do not cover are counted the same way by the gate."""


# ---------- Korean helpers ----------


def _batchim(word: str) -> tuple[bool, bool]:
    """(ends with a final consonant, that consonant is ㄹ). Squares and SAN end in a digit:
    0 1 3 6 7 8 carry a final consonant (영 일 삼 육 칠 팔), 1 7 8 end in ㄹ."""
    word = word.rstrip("+#!?.)")
    if not word:
        return False, False
    last = word[-1]
    if last.isdigit():
        return last in "013678", last in "178"
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        return jong != 0, jong == 8
    if last.isalpha():
        return last.lower() in "lmn", last.lower() == "l"
    return False, False


def josa(word: str, kind: Literal["이", "을", "은", "과", "로"]) -> str:
    """Attach a Korean particle to a square, SAN or label with the right allomorph."""
    has, rieul = _batchim(word)
    match kind:
        case "이":
            return word + ("이" if has else "가")
        case "을":
            return word + ("을" if has else "를")
        case "은":
            return word + ("은" if has else "는")
        case "과":
            return word + ("과" if has else "와")
        case "로":
            return word + ("로" if (not has or rieul) else "으로")
    raise ValueError(kind)


def fmt_score(score: Score) -> str:
    """'+0.4', '-2.1' or '#3' from White's point of view (the eval bar convention)."""
    if score.mate is not None:
        return f"#{score.mate}"
    return f"{(score.cp or 0) / 100:+.1f}"


def move_label(fen: str, san: str) -> str:
    """'20… Qd7' for Black, '21. Nxf6+' for White, in the mockup's style."""
    board = chess.Board(fen)
    n = board.fullmove_number
    return f"{n}. {san}" if board.turn == chess.WHITE else f"{n}… {san}"


def numbered_line(fen: str, sans: list[str]) -> tuple[str, list[Claim]]:
    """'21.Nxf6+ Bxf6 22.Rxd7' plus one legal_move claim per ply. The text stops at the first
    illegal move but its claim stays, so a broken line fails verification."""
    board = chess.Board(fen)
    parts: list[str] = []
    claims: list[Claim] = []
    for i, san in enumerate(sans):
        claims.append(Claim(kind="legal_move", fen=board.fen(), object=san))
        try:
            move = board.parse_san(san)
        except ValueError:
            break
        n = board.fullmove_number
        if board.turn == chess.WHITE:
            parts.append(f"{n}.{san}")
        elif i == 0:
            parts.append(f"{n}...{san}")
        else:
            parts.append(san)
        board.push(move)
    return " ".join(parts), claims


def piece_label(board: chess.Board, square: str) -> str:
    """'Qd7' style label for the piece on a square; pawns read 'd5 폰', empty squares stay."""
    piece = board.piece_at(chess.parse_square(square))
    if piece is None:
        return square
    if piece.piece_type == chess.PAWN:
        return f"{square} 폰"
    return f"{piece.symbol().upper()}{square}"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + josa(items[-2], "과")[len(items[-2]) :] + " " + items[-1]


def _piece_claim(board: chess.Board, square: str) -> Claim | None:
    piece = board.piece_at(chess.parse_square(square))
    if piece is None:
        return None
    return Claim(kind="piece_on", fen=board.fen(), subject=square, object=piece.symbol())


def _claimed_squares(claims: list[Claim]) -> set[str]:
    """Squares a sentence's claims put on the board: the subject/object of every claim plus
    both ends of every legal_move SAN (playing 'Nxf6+' is a statement about d5 and f6)."""
    out: set[str] = set()
    for claim in claims:
        for value in (claim.subject, claim.object):
            if value in chess.SQUARE_NAMES:
                out.add(str(value))
        if claim.kind != "legal_move" or claim.object is None:
            continue
        try:
            board = chess.Board(claim.fen)
            move = board.parse_san(claim.object)
        except ValueError:
            continue
        out.add(chess.square_name(move.from_square))
        out.add(chess.square_name(move.to_square))
    return out


def unclaimed_squares(text: str, claims: list[Claim]) -> int:
    """How many squares a sentence names that none of its claims covers.

    A sentence that says 'Rd1이 d7을 노립니다' with no attacks claim states a board fact the
    verifier never sees, so it counts as failed. The rule applies to both writers: a template
    sentence with a forgotten claim is no more checkable than an LLM one."""
    named = set(_SQUARE_MENTION.findall(_QUOTED.sub("", text)))
    return len(named - _claimed_squares(claims))


def _line_kind(a: chess.Square, b: chess.Square) -> str:
    if chess.square_file(a) == chess.square_file(b):
        return f"{chess.FILE_NAMES[chess.square_file(a)]}파일"
    if chess.square_rank(a) == chess.square_rank(b):
        return f"{chess.square_rank(a) + 1}랭크"
    return "대각선"


# ---------- template sentences ----------


def _lead_sentences(facts: ReviewFacts) -> list[Sentence]:
    out: list[Sentence] = []
    natural_holds = False
    if facts.natural_reason:
        natural = Sentence(facts.natural_reason, list(facts.natural_claims))
        natural_holds = _holds(natural)
        out.append(natural)
    refutation = facts.refutation
    if refutation is not None and refutation.main_line and facts.fen_punished:
        # '자연스러운 반응이지만' only makes sense after the sentence it refers back to, and
        # that sentence is dropped when its own claims fail.
        geometry = _discovery_geometry(facts, natural_holds)
        if geometry is not None:
            out.append(geometry)
        else:
            opener = "자연스러운 반응이지만" if natural_holds else "하지만"
            out.append(
                Sentence(
                    f"{opener} {josa(refutation.main_line[0], '이')} 있습니다. "
                    f"평가가 {fmt_score(facts.eval_before)}에서 "
                    f"{fmt_score(facts.eval_after)}로 바뀝니다.",
                    [Claim(kind="legal_move", fen=facts.fen_after, object=refutation.main_line[0])],
                )
            )
    elif not (facts.natural_reason and chess.Board(facts.fen_after).is_checkmate()):
        out.append(_eval_sentence(facts))
    return out


def _eval_text(facts: ReviewFacts) -> str:
    """The evaluation in one clause. A delivered mate is stored as ±100 pawns, and
    '이 수 뒤 평가는 +100.0입니다' is a number no position ever has."""
    if chess.Board(facts.fen_after).is_checkmate():
        return "이 수로 체크메이트입니다."
    return f"이 수 뒤 평가는 {fmt_score(facts.eval_after)}입니다."


def _eval_sentence(facts: ReviewFacts) -> Sentence:
    text = _eval_text(facts)
    if text.endswith("체크메이트입니다."):
        return Sentence(text, [Claim(kind="checkmate", fen=facts.fen_after)])
    claims: list[Claim] = []
    if facts.classification == "best":
        # The deeper confirmation search can score the played move at least as well as the
        # MultiPV best of the shallow search, so 'best' does not imply the same move.
        if facts.best_san and facts.best_san != facts.san:
            text += f" 엔진 최선 {josa(facts.best_san, '과')} 차이가 없습니다."
            claims.append(Claim(kind="legal_move", fen=facts.fen_before, object=facts.best_san))
        else:
            text += " 엔진 최선 수와 같습니다."
    elif facts.classification == "book":
        text += " 오프닝 책에 있는 수입니다."
    elif facts.classification == "forced":
        text += " 둘 수 있는 유일한 수였습니다."
    elif facts.best_san and facts.best_san != facts.san:
        text += f" 엔진 최선은 {facts.best_san}입니다."
        claims.append(Claim(kind="legal_move", fen=facts.fen_before, object=facts.best_san))
    return Sentence(text, claims)


def _discovery_geometry(facts: ReviewFacts, natural_holds: bool = False) -> Sentence | None:
    """'Qd7이 Rd1과 같은 d파일에 놓입니다. 지금은 Nd5가 사이를 가리고 있을 뿐입니다.'
    Built from a discovered-attack motif of the punishing move: attacker, target and the
    square the punishing piece leaves. Every square named becomes a claim."""
    assert facts.refutation is not None and facts.fen_punished is not None
    motif = next((m for m in facts.refutation.motifs if m.kind == "discovered_attack"), None)
    if motif is None or not motif.targets:
        return None
    before = chess.Board(facts.fen_after)
    try:
        punishing = before.parse_san(facts.refutation.main_line[0])
    except ValueError:
        return None
    attacker, target = motif.attacker, motif.targets[0]
    blocker = chess.square_name(punishing.from_square)
    a_sq, t_sq = chess.parse_square(attacker), chess.parse_square(target)
    between = chess.SquareSet(chess.between(a_sq, t_sq))
    if punishing.from_square not in between:
        return None
    claims: list[Claim] = []
    for square in (attacker, target, blocker):
        claim = _piece_claim(before, square)
        if claim is None:
            return None
        claims.append(claim)
    for sq in between:
        if sq != punishing.from_square:
            claims.append(
                Claim(kind="square_empty", fen=facts.fen_after, subject=chess.square_name(sq))
            )
    claims.append(Claim(kind="attacks", fen=facts.fen_punished, subject=attacker, object=target))
    opener = "자연스러운 반응이지만," if natural_holds else "하지만"
    text = (
        f"{opener} {josa(piece_label(before, target), '이')} "
        f"{josa(piece_label(before, attacker), '과')} 같은 {_line_kind(a_sq, t_sq)}에 놓입니다. "
        f"지금은 {josa(piece_label(before, blocker), '이')} 사이를 가리고 있을 뿐입니다."
    )
    return Sentence(text, claims)


def _threat_claims(fen: str, mating_san: str) -> list[Claim]:
    """'X mates if the opponent passes': the move is legal after a null move and the position
    it reaches is checkmate. An empty list when the SAN does not fit, so the sentence fails."""
    board = chess.Board(fen)
    if board.is_check() or board.is_game_over():
        return []
    board.push(chess.Move.null())
    passed = board.fen()
    try:
        board.push_san(mating_san)
    except ValueError:
        return [Claim(kind="legal_move", fen=passed, object=mating_san)]
    return [
        Claim(kind="legal_move", fen=passed, object=mating_san),
        Claim(kind="checkmate", fen=board.fen()),
    ]


def _motif_sentence(facts: ReviewFacts, motif: MotifOut) -> Sentence | None:
    """One sentence for a motif of the punishing move, with the attack facts it names."""
    assert facts.refutation is not None and facts.fen_punished is not None
    punish_san = facts.refutation.main_line[0]
    label = facts.punish_label or move_label(facts.fen_after, punish_san)
    before = chess.Board(facts.fen_after)
    after = chess.Board(facts.fen_punished)
    fen = facts.fen_punished
    claims: list[Claim] = [Claim(kind="legal_move", fen=facts.fen_after, object=punish_san)]

    def named(square: str) -> str:
        return piece_label(after if after.piece_at(chess.parse_square(square)) else before, square)

    def attacks(subject: str, obj: str) -> None:
        claims.append(Claim(kind="attacks", fen=fen, subject=subject, object=obj))
        for square in (subject, obj):
            claim = _piece_claim(after, square)
            if claim is not None:
                claims.append(claim)

    targets = motif.targets
    opponent = COLOR_KO["white" if after.turn == chess.WHITE else "black"]
    check_tail = ""
    if motif.with_check:
        claims.append(Claim(kind="is_check", fen=fen))
        check_tail = " 체크입니다."
    match motif.kind:
        case "discovered_attack" if targets:
            attacks(motif.attacker, targets[0])
            a, t = named(motif.attacker), named(targets[0])
            if motif.with_check:
                text = (
                    f"{label}: 체크를 주면서 비켜서고, 그 순간 {josa(a, '이')} {josa(t, '을')} "
                    f"겨냥합니다. {opponent}은 체크부터 처리해야 합니다."
                )
            else:
                m = named(motif.mover)
                text = (
                    f"{label}: {josa(m, '이')} 비켜서며 {josa(a, '이')} {josa(t, '을')} 겨냥합니다."
                )
        case "fork" if len(targets) >= 2:
            for t in targets:
                attacks(motif.mover, t)
            piece = after.piece_at(chess.parse_square(motif.mover))
            head = PIECE_KOREAN.get(piece.symbol().upper(), "기물") if piece else "기물"
            joined = _join([named(t) for t in targets])
            text = f"{label}: {head} 포크로 {josa(joined, '을')} 동시에 공격합니다.{check_tail}"
        case "pin" | "skewer" if len(targets) == 2:
            attacks(motif.attacker, targets[0])
            # The piece behind and the empty squares between are what make it a pin, so they
            # are claimed too; the attacks claim alone leaves half the sentence unchecked.
            behind_claim = _piece_claim(after, targets[1])
            if behind_claim is not None:
                claims.append(behind_claim)
            front_sq, behind_sq = (chess.parse_square(t) for t in targets)
            for sq in chess.SquareSet(chess.between(front_sq, behind_sq)):
                claims.append(Claim(kind="square_empty", fen=fen, subject=chess.square_name(sq)))
            a, front, behind = named(motif.attacker), named(targets[0]), named(targets[1])
            if motif.kind == "pin":
                text = (
                    f"{label}: {josa(a, '이')} {josa(front, '을')} {behind}에 묶습니다.{check_tail}"
                )
            else:
                text = (
                    f"{label}: {josa(a, '이')} {josa(front, '을')} 공격하고, "
                    f"그 뒤에 {josa(behind, '이')} 있습니다.{check_tail}"
                )
        case "hanging_piece" | "trapped_piece" if targets:
            attacks(motif.attacker, targets[0])
            a, t = named(motif.attacker), named(targets[0])
            tail = " 피할 칸이 없습니다." if motif.kind == "trapped_piece" else ""
            text = f"{label}: {josa(a, '이')} {josa(t, '을')} 공격합니다.{tail}{check_tail}"
        case "remove_defender" if len(targets) == 2:
            defender, protected = named(targets[0]), named(targets[1])
            if motif.mover == targets[0]:
                claim = _piece_claim(after, motif.mover)
                if claim is not None:
                    claims.append(claim)
                text = (
                    f"{label}: {josa(protected, '을')} 지키던 {josa(defender, '을')} "
                    f"잡습니다.{check_tail}"
                )
            else:
                attacks(motif.mover, targets[0])
                text = (
                    f"{label}: {josa(protected, '을')} 지키는 {josa(defender, '을')} "
                    f"공격합니다.{check_tail}"
                )
        case "overload" if len(targets) == 3:
            attacks(motif.attacker, targets[1])
            for protected in targets[1:]:
                claims.append(Claim(kind="defends", fen=fen, subject=targets[0], object=protected))
            d, p1, p2 = (named(t) for t in targets)
            text = (
                f"{label}: {josa(d, '이')} {josa(p1, '과')} {josa(p2, '을')} 함께 지키는데, "
                f"{josa(named(motif.attacker), '이')} {josa(p1, '을')} 공격합니다.{check_tail}"
            )
        case "back_rank" | "mate_threat" if motif.line:
            claim = _piece_claim(after, motif.attacker)
            if claim is not None:
                claims.append(claim)
            # The mate is a threat: it is the mover's move in a position where the opponent
            # is to move, so it is claimed on the position after a pass, which is what
            # 'threatens mate' means. parse_san ignores the '#', hence the checkmate claim.
            claims.extend(_threat_claims(fen, motif.line[0]))
            text = f"{label}: {motif.line[0]} 메이트를 위협합니다.{check_tail}"
        case _:
            if motif.attacker != motif.mover or not targets:
                return None
            attacks(motif.attacker, targets[0])
            text = f"{label}: {motif.description}.{check_tail}"
    return Sentence(text, claims)


def _main_line_sentence(facts: ReviewFacts) -> Sentence | None:
    assert facts.refutation is not None
    line = facts.refutation.main_line
    if len(line) < 2:
        return None
    text, claims = numbered_line(facts.fen_after, line[:6])
    return Sentence(f"이어지는 수순: {text}.", claims)


BRANCH_SENTENCES = 2
"""Branches put in prose. Three near-identical '…로 끝납니다' lines is the repetition a reader
notices; the rest of the branches stay in the payload for the move list."""


def _branch_sentences(facts: ReviewFacts) -> list[Sentence]:
    assert facts.refutation is not None
    out: list[Sentence] = []
    if not facts.fen_punished:
        return out
    board = chess.Board(facts.fen_punished)
    mover = board.turn
    main = facts.refutation.main_line
    branches = facts.refutation.branches
    for branch in branches:
        if len(out) >= BRANCH_SENTENCES or not branch.moves:
            continue
        if list(branch.moves) == main[1 : 1 + len(branch.moves)]:
            continue  # the main-line sentence already showed exactly this
        # '같은 결과' points at the first branch; when that one was not printed, say the
        # outcome itself instead of referring back to a sentence the reader never saw.
        result = branches[0].result if not out else branch.result
        reply = move_label(facts.fen_punished, branch.moves[0])
        claims = [Claim(kind="legal_move", fen=facts.fen_punished, object=branch.moves[0])]
        probe = board.copy()
        try:
            probe.push_san(branch.moves[0])
        except ValueError:
            out.append(Sentence(f"{reply}: {result}.", claims))
            continue
        rest, rest_claims = numbered_line(probe.fen(), branch.moves[1:])
        claims.extend(rest_claims)
        end = _replay(probe, branch.moves[1:])
        if end is not None and end.is_checkmate() and end.turn != mover:
            # The side that played branch.moves[0] is the one mating, so the usual 'X에는 Y가
            # 있어' framing (Y refutes X) would invert the line as well as mislabel it.
            claims.append(Claim(kind="checkmate", fen=end.fen()))
            text = (
                f"{reply}: {josa(rest, '로')} 메이트를 만듭니다." if rest else f"{reply}: 메이트."
            )
        elif rest:
            text = f"{reply}에는 {josa(rest, '이')} 있어 {josa(result, '로')} 끝납니다."
        else:
            text = f"{reply}: {josa(result, '로')} 끝납니다."
        out.append(Sentence(text, claims))
    return out


def _replay(board: chess.Board, sans: list[str]) -> chess.Board | None:
    probe = board.copy()
    for san in sans:
        try:
            probe.push_san(san)
        except ValueError:
            return None
    return probe


def _note_sentence(facts: ReviewFacts) -> Sentence | None:
    if facts.refutation is None or not facts.refutation.note:
        return None
    claims: list[Claim] = []
    if facts.refutation.main_line:
        # The note names the punishment it recommends as well as the one it rejects.
        claims.append(
            Claim(kind="legal_move", fen=facts.fen_after, object=facts.refutation.main_line[0])
        )
    if len(facts.note_line) >= 2:
        board = chess.Board(facts.fen_after)
        claims.append(Claim(kind="legal_move", fen=facts.fen_after, object=facts.note_line[0]))
        try:
            board.push_san(facts.note_line[0])
        except ValueError:
            return Sentence(facts.refutation.note, claims, unresolved=1)
        claims.append(Claim(kind="legal_move", fen=board.fen(), object=facts.note_line[1]))
    return Sentence(facts.refutation.note, claims)


def _guessed_alternative_claims(
    facts: ReviewFacts, board: chess.Board, alt: Alternative
) -> list[Claim]:
    """Claims for an Alternative that carries none of its own (a payload built before
    explain_alternative returned them): the move itself and the piece it takes."""
    claims = [Claim(kind="legal_move", fen=facts.fen_before, object=alt.san)]
    try:
        move = board.parse_san(alt.san)
    except ValueError:
        return claims
    if board.is_capture(move) and not board.is_en_passant(move):
        claim = _piece_claim(board, chess.square_name(move.to_square))
        if claim is not None:
            claims.append(claim)
    return claims


def _alternative_sentences(facts: ReviewFacts) -> list[Sentence]:
    out: list[Sentence] = []
    board = chess.Board(facts.fen_before)
    for alt in facts.alternatives:
        if alt.san == facts.san:
            continue
        # reasoning.explain_alternative writes the prose and hands over a claim for every
        # fact in it, so the two cannot drift; the fallback below only covers old payloads.
        claims = list(alt.claims) or _guessed_alternative_claims(facts, board, alt)
        tag = "엔진 최선" if alt.is_best else "차선"
        why = (
            _SAN_PREFIX.sub("", alt.why, count=1) if alt.why.startswith(f"{alt.san}:") else alt.why
        )
        label = move_label(facts.fen_before, alt.san)
        text = f"{tag} {label} ({fmt_score(alt.eval)})"
        text += f": {why}" if why else "."
        out.append(Sentence(text, claims))
    return out


def _comparison_sentence(facts: ReviewFacts) -> Sentence | None:
    cmp = facts.comparison
    if cmp is None or not cmp.summary:
        return None
    claims = [
        Claim(kind="legal_move", fen=facts.fen_before, object=san) for san in (cmp.a_san, cmp.b_san)
    ]
    return Sentence(cmp.summary, claims)


def _human_sentences(facts: ReviewFacts) -> list[Sentence]:
    human = facts.human
    if human is None:
        return []
    out: list[Sentence] = []
    played_claim = Claim(kind="legal_move", fen=facts.fen_before, object=facts.san)
    source = SOURCE_KO.get(human.source or "")
    if human.played_prob is not None and source:
        pct = human.played_prob * 100
        if pct < 1:
            shown = "1% 미만"
        elif pct < 10:
            shown = f"약 {pct:.1f}%"
        else:
            shown = f"약 {pct:.0f}%"
        out.append(
            Sentence(
                f"{source}으로 {human.rating} 구간에서 {josa(facts.move_label, '을')} 두는 비율은 "
                f"{shown}입니다.",
                [played_claim],
            )
        )
    if human.computer_move and facts.best_san and facts.best_san != facts.san:
        best = move_label(facts.fen_before, facts.best_san)
        out.append(
            Sentence(
                f"엔진 최선 {josa(best, '은')} 이 구간에서 거의 나오지 않는 컴퓨터 수입니다.",
                [Claim(kind="legal_move", fen=facts.fen_before, object=facts.best_san)],
            )
        )
    return out


def _strategy_sentence(facts: ReviewFacts) -> Sentence | None:
    if not facts.strategy_note:
        return None
    board = chess.Board(facts.fen_before)
    dots = "." if board.turn == chess.WHITE else "..."
    text = facts.strategy_note.replace(
        f"{board.fullmove_number}{dots}{facts.san}", facts.move_label
    )
    text = text.rstrip(".") + "."
    if facts.structure_name:
        text = f"구조는 {facts.structure_name}입니다. " + text
    claims = [Claim(kind="legal_move", fen=facts.fen_before, object=facts.san)]
    if facts.best_san and facts.best_san in text:
        # The note can recommend the engine move ('엔진 최선 Nxd5는 계획 ...의 수입니다').
        claims.append(Claim(kind="legal_move", fen=facts.fen_before, object=facts.best_san))
    return Sentence(text, claims)


def _body_sentences(facts: ReviewFacts) -> list[Sentence]:
    out: list[Sentence] = []
    if facts.refutation is not None and facts.refutation.main_line and facts.fen_punished:
        for motif in facts.refutation.motifs:
            sentence = _motif_sentence(facts, motif)
            if sentence is not None:
                out.append(sentence)
        main = _main_line_sentence(facts)
        if main is not None:
            out.append(main)
        out.extend(_branch_sentences(facts))
        note = _note_sentence(facts)
        if note is not None:
            out.append(note)
    out.extend(_alternative_sentences(facts))
    cmp = _comparison_sentence(facts)
    if cmp is not None:
        out.append(cmp)
    out.extend(_human_sentences(facts))
    strategy = _strategy_sentence(facts)
    if strategy is not None:
        out.append(strategy)
    return out


def headline(facts: ReviewFacts) -> str:
    return f"{facts.move_label} {CLASS_KO.get(facts.classification, facts.classification)}"


# ---------- the gate ----------


def _verdicts(claims: list[Claim]) -> list[Verdict]:
    out: list[Verdict] = []
    for claim in claims:
        try:
            out.append(verify(claim))
        except ValueError as exc:  # unreadable FEN
            out.append(Verdict(claim=claim, holds=False, detail=str(exc)))
    return out


def _holds(sentence: Sentence) -> bool:
    """Whether this sentence would survive the gate on its own."""
    if sentence.unresolved or unclaimed_squares(sentence.text, sentence.claims):
        return False
    return all(verdict.holds for verdict in _verdicts(sentence.claims))


def _gate(
    facts: ReviewFacts,
    head: str,
    lead: list[Sentence],
    body: list[Sentence],
    source: Literal["llm", "template"],
) -> Explanation:
    """Verify every claim, drop sentences with a failing one or with a board fact no claim
    covers, and count the rest. The fallback lead goes through the same check."""
    kept_lead: list[str] = []
    kept_body: list[str] = []
    kept_claims: list[Claim] = []
    total = 0
    holding = 0
    dropped = 0
    for group, kept in ((lead, kept_lead), (body, kept_body)):
        for sentence in group:
            verdicts = _verdicts(sentence.claims)
            missing = sentence.unresolved + unclaimed_squares(sentence.text, sentence.claims)
            total += len(verdicts) + missing
            holding += sum(1 for v in verdicts if v.holds)
            if missing or not all(v.holds for v in verdicts):
                dropped += 1
                continue
            kept.append(sentence.text)
            kept_claims.extend(sentence.claims)
    if not kept_lead:
        # Every lead sentence failed. What is left is the evaluation itself, which is engine
        # data rather than a board fact, so it needs no claim; the best-move clause of
        # _eval_sentence does need one and is not repeated here.
        kept_lead.append(_eval_text(facts))
    return Explanation(
        headline=head,
        lead=" ".join(kept_lead),
        sentences=kept_body,
        claims=kept_claims,
        verified=total > 0 and dropped == 0,
        verified_claims=holding,
        total_claims=total,
        source=source,
    )


def template_explanation(facts: ReviewFacts) -> Explanation:
    """Deterministic Korean prose built only from the facts, verified sentence by sentence."""
    return _gate(facts, headline(facts), _lead_sentences(facts), _body_sentences(facts), "template")


# ---------- LLM ----------

LLM_SYSTEM = "\n".join(
    [
        "당신은 체스 튜터의 문장 작성기입니다. 체스 판단은 하지 않습니다.",
        "",
        "규칙:",
        "1. 입력 JSON의 사실(모티프, 수순, 평가, 사람 관점)만 한국어 문장으로 서술합니다. "
        "JSON에 없는 수, 칸, 기물, 위협을 지어내지 않습니다.",
        "2. 모든 전술적 주장은 구체적 수순과 함께 씁니다. 수는 JSON에 있는 SAN 그대로 적습니다.",
        "3. 문장마다 그 문장이 기대는 보드 사실을 claims로 나열합니다. 검증기가 각 claim을 "
        "보드와 대조하고, 하나라도 틀리면 그 문장은 출력되지 않습니다.",
        "   - kind: attacks(subject 칸의 기물이 object 칸을 공격), defends(같은 색 기물 보호), "
        "is_check(그 국면이 체크), checkmate(그 국면이 체크메이트), piece_on(subject 칸에 "
        "object 기물 기호, 백은 대문자 흑은 소문자), square_empty, "
        "legal_move(object가 그 국면에서 둘 수 있는 SAN)",
        "   - position: JSON의 positions 키 중 하나(before, after, punished 등). 문장에 나오는 "
        "칸은 모두 그 문장의 claims가 덮어야 합니다(claim의 subject/object이거나 legal_move SAN이 "
        "지나는 칸). 덮이지 않은 칸이 하나라도 있으면 그 문장은 버려집니다.",
        "4. 사람 수준에서 설명하기 어려운 엔진 수는 컴퓨터 수라고 부릅니다"
        "(human.computer_move가 true일 때만).",
        "5. 계획은 폰 구조 단위로 말합니다(structure_name, strategy_note가 있을 때만).",
        "6. 어조: 목업처럼 짧고 담백하게. 존댓말. 줄표(—)는 쓰지 않습니다.",
        "7. headline은 move_label과 분류를 그대로 씁니다. lead는 두세 문장으로 이 수가 무엇을 "
        "하려 했고 왜 실패하는지(또는 왜 좋은지) 요약하고 lead_claims를 함께 냅니다. "
        "sentences는 반박 수순, 분기, 대안, 비교, 사람 관점 순서로 씁니다.",
    ]
)


class LLMClaim(BaseModel):
    kind: ClaimKind
    position: str
    subject: str | None
    object: str | None


class LLMSentence(BaseModel):
    text: str
    claims: list[LLMClaim]


class LLMOutput(BaseModel):
    headline: str
    lead: str
    lead_claims: list[LLMClaim]
    sentences: list[LLMSentence]


def _sanitize(text: str) -> str:
    return " ".join(text.replace(" — ", ", ").replace("—", ",").split())


def _resolve(facts: ReviewFacts, text: str, claims: list[LLMClaim]) -> Sentence:
    """Map position keys to FENs. A claim on an unknown position counts as unresolved and the
    sentence is dropped; squares the surviving claims do not cover are caught by the gate,
    which applies the same rule to template sentences."""
    text = _sanitize(text)
    resolved: list[Claim] = []
    unresolved = 0
    for claim in claims:
        fen = facts.positions.get(claim.position)
        if fen is None:
            unresolved += 1
            continue
        resolved.append(Claim(kind=claim.kind, fen=fen, subject=claim.subject, object=claim.object))
    return Sentence(text, resolved, unresolved=unresolved)


def _call_llm(facts: ReviewFacts) -> LLMOutput | None:
    """One structured-output call. Raises on any SDK or network problem."""
    import anthropic

    settings = get_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key, timeout=LLM_TIMEOUT, max_retries=1
    )
    response = client.messages.parse(
        model=settings.anthropic_model,
        max_tokens=LLM_MAX_TOKENS,
        system=LLM_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": json.dumps(facts.model_dump(mode="json"), ensure_ascii=False),
            }
        ],
        output_format=LLMOutput,
    )
    parsed = response.parsed_output
    return parsed if isinstance(parsed, LLMOutput) else None


def llm_explanation(facts: ReviewFacts) -> Explanation | None:
    """Claude's phrasing of the facts, verified like the template. None without an API key,
    on any exception, or when the model returned nothing usable."""
    if not get_settings().anthropic_api_key:
        return None
    try:
        output = _call_llm(facts)
    except Exception as exc:  # noqa: BLE001 - the template is the fallback, never an error
        log.warning("llm explanation failed for game %s ply %s: %s", facts.game_id, facts.ply, exc)
        return None
    if output is None:
        return None
    head = _sanitize(output.headline)
    if facts.san not in head:
        head = headline(facts)
    lead = [_resolve(facts, output.lead, output.lead_claims)] if output.lead.strip() else []
    body = [_resolve(facts, s.text, s.claims) for s in output.sentences if s.text.strip()]
    return _gate(facts, head, lead, body, "llm")


def explain(facts: ReviewFacts) -> Explanation:
    """LLM text when it is available and kept at least MIN_LLM_SENTENCES, else the template."""
    llm = llm_explanation(facts)
    if llm is not None and len(llm.sentences) >= MIN_LLM_SENTENCES:
        return llm
    return template_explanation(facts)
