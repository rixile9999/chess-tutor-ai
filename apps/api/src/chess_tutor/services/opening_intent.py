"""What a move does, as facts anyone can check (layer 3, plan §5.3).

:func:`annotate` replays a line and says, for every move, what it does on the board: the name
it reaches, the centre it takes, the piece it develops, the tension it makes, the setup it
fills, the plan it starts. Each detector states python-chess facts only and hands over every
square it named as a :class:`~chess_tutor.verify.Claim`, so ``verify.verify_all`` checks the
sentence before anyone reads it (principle 1); a sentence whose claims do not all hold is still
reported as a fact, with ``verified=False``, and left out of ``MoveAnnotation.text``.

Order and length: name → motif/tension/gambit → centre/development/castling/fianchetto →
prophylaxis → plan/setup → naturalness → engine, at most MAX_SENTENCES sentences a move, and
centre and development merge into one sentence when both fire ("e5 폰을 공격하며 첫 기물을
전개합니다"). The first sentence is the one the journal shows on its single line (plan §9.3).

Cost: the detectors are pure python-chess, well under a millisecond a move. Maia runs only for
``naturalness=True`` and the engine (`play_coach.check`, depth `play_depth`) only for
``engine="always"`` or an off-book move under ``engine="off_book"``, at most
ANNOTATE_ENGINE_CAP moves a request.

The engine judgement borrows a pooled engine for the length of one `check` call and gives it
straight back, so the next move of the same request gets the same warm process. Holding a
borrow of this module's own around the whole loop (plan §5.5) would take a second slot from the
pool for the length of the request, and with the default two slots two concurrent requests
would each wait for the other's engine until the borrow times out; one borrow per call is what
`play_coach` already does and what this module therefore leaves it to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from chess_tutor.motifs import Motif, describe, detect
from chess_tutor.openings import find_rows, lookup, moves_of, name_ko, next_moves
from chess_tutor.schemas import (
    AnnotateRequest,
    Classification,
    Color,
    EngineMode,
    FactKind,
    MoveAnnotation,
    MoveFact,
    NamedCandidate,
    PlayCheckRequest,
    PlayCheckResponse,
)
from chess_tutor.services import maia, opening_guide, play_coach, setups
from chess_tutor.services import plans as kb
from chess_tutor.services.openings_map import move_label
from chess_tutor.services.reasoning import _eul, _ga, _is_break, _ro, _wa
from chess_tutor.services.sentences import Assembly, Sentence, assemble
from chess_tutor.structure import classify
from chess_tutor.verify import Claim

ANNOTATE_ENGINE_CAP = 6
"""Engine calls one request may make (plan §5.3). A strip jump can send twenty moves at once;
the moves past the cap come back with ``engine=None`` and the page offers a button."""
MAX_SENTENCES = 4
BOOK_ALTERNATIVES = 3
CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
LONG_DIAGONAL = (chess.B2, chess.G2, chess.B7, chess.G7)
FIANCHETTO_PAWNS: dict[chess.Square, chess.Square] = {
    chess.B3: chess.C1,
    chess.G3: chess.F1,
    chess.B6: chess.C8,
    chess.G6: chess.F8,
}
"""Pawn move -> the home square of the bishop it makes room for."""
EARLY_QUEEN_PIECES = 1
"""Developed minor pieces up to which a queen move counts as an early sortie."""
MOTIF_KINDS = ("pin", "fork", "discovered_attack")
"""Motifs this module speaks: the ones `motifs.describe` states in squares and pieces alone."""
SETUP_FACTS = 1
"""Setup changes a move reports: the journal has room for one, the setup panel shows the rest."""

ORDINALS_KO = ("첫", "두", "세", "네", "다섯", "여섯", "일곱", "여덟")
PIECE_KO: dict[chess.PieceType, str] = {
    chess.KING: "킹",
    chess.QUEEN: "퀸",
    chess.ROOK: "룩",
    chess.BISHOP: "비숍",
    chess.KNIGHT: "나이트",
    chess.PAWN: "폰",
}
CLASSIFICATION_KO: dict[Classification, str] = {
    "book": "책 수",
    "best": "최선",
    "good": "괜찮은 수",
    "inaccuracy": "부정확",
    "mistake": "실수",
    "blunder": "심각한 실수",
    "forced": "강제된 수",
}

PRIORITY: dict[FactKind, int] = {
    "name": 0,
    "book": 0,
    "transposition": 1,
    "motif": 2,
    "tension": 2,
    "break": 2,
    "gambit": 2,
    "center": 3,
    "development": 3,
    "castling": 3,
    "fianchetto": 3,
    "prophylaxis": 4,
    "plan": 5,
    "setup": 5,
    "naturalness": 6,
    "engine": 7,
}

BAD_FEN = "FEN을 읽을 수 없습니다"
ILLEGAL_POSITION = "체스 규칙에 맞지 않는 국면입니다."
ILLEGAL_MOVE = "이 국면에서 둘 수 없는 수입니다"

_SQUARE_MENTION = re.compile(r"[a-h][1-8]")
"""Same rule as services.verbalize: a square named in a sentence needs a claim that covers it."""


_Fact = tuple[FactKind, Sentence]


@dataclass(frozen=True)
class _Move:
    """One move with the two positions around it, so every detector shares the same boards."""

    before: chess.Board
    after: chess.Board
    move: chess.Move
    san: str
    ply: int
    color: chess.Color
    piece: chess.Piece

    @property
    def to_square(self) -> chess.Square:
        return self.move.to_square


# ---------- small helpers ----------


def _board(fen: str | None) -> chess.Board:
    """The position, refused when it breaks the rules. Raises ValueError either way."""
    if fen is None:
        return chess.Board()
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"{BAD_FEN}: {exc}") from exc
    if not board.is_valid():
        raise ValueError(ILLEGAL_POSITION)
    return board


def _percent(prob: float) -> str:
    """A move played once in five hundred games should not read as '0%'."""
    pct = prob * 100
    return f"{pct:.1f}%" if 0.0 < pct < 10.0 else f"{pct:.0f}%"


def _join_ko(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{_wa(items[0])} {_join_ko(items[1:])}"


def _named(board: chess.Board, square: chess.Square) -> str:
    """'e5 폰' — a square together with the piece standing on it."""
    piece = board.piece_at(square)
    if piece is None:
        return chess.square_name(square)
    return f"{chess.square_name(square)} {PIECE_KO[piece.piece_type]}"


def _piece_claim(board: chess.Board, square: chess.Square) -> Claim:
    """What stands on this square (or that it is empty), as the verifier reads it."""
    piece = board.piece_at(square)
    name = chess.square_name(square)
    if piece is None:
        return Claim(kind="square_empty", fen=board.fen(), subject=name)
    return Claim(kind="piece_on", fen=board.fen(), subject=name, object=piece.symbol())


def _attack_claim(board: chess.Board, frm: chess.Square, to: chess.Square) -> Claim:
    return Claim(
        kind="attacks",
        fen=board.fen(),
        subject=chess.square_name(frm),
        object=chess.square_name(to),
    )


def _subject_particle(word: str) -> str:
    """'이' or '가' for a word the sentence puts something in brackets after: the particle
    agrees with the name, not with the ECO code in between ('루이 로페즈(C60)가')."""
    return _ga(word)[len(word) :]


def _ground(text: str, board: chess.Board) -> list[Claim]:
    """A claim for every square a knowledge-base sentence names, so it can be verified at all."""
    squares = sorted({name for name in _SQUARE_MENTION.findall(text)})
    return [_piece_claim(board, chess.parse_square(name)) for name in squares]


def _developed(board: chess.Board, color: chess.Color) -> int:
    """Knights and bishops of `color` that have left their starting squares."""
    start = chess.Board()
    return sum(
        1
        for piece_type in (chess.KNIGHT, chess.BISHOP)
        for square in board.pieces(piece_type, color)
        if start.piece_at(square) != chess.Piece(piece_type, color)
    )


def _at_home(board: chess.Board, square: chess.Square) -> bool:
    return chess.Board().piece_at(square) == board.piece_at(square)


# ---------- detectors ----------


def _book_facts(mv: _Move, in_book: bool, alternatives: list[NamedCandidate]) -> list[_Fact]:
    """The book's own word: the name the move reaches, or that the move leaves the book.

    Names are Korean (`openings.name_ko`, plan §10.5) and carry the ECO code instead of quote
    marks: '루이 로페즈(C60)가 됩니다'. Sixty-four names still hold a move ('Benko Gambit: Nd2
    Variation'), so whatever square the Korean label names is grounded like any other square —
    a name is a label, not a claim about this board, and the claim just says what stands there."""
    out: list[_Fact] = []
    here = lookup(mv.before)
    arrived = lookup(mv.after)
    if arrived is not None and (here is None or arrived.name != here.name):
        korean = name_ko(arrived.name)
        out.append(
            (
                "name",
                Sentence(
                    f"{korean}({arrived.eco}){_subject_particle(korean)} 됩니다.",
                    _ground(korean, mv.after),
                ),
            )
        )
    if in_book:
        return out
    if alternatives:
        listed = ", ".join(
            f"{c.san}({c.name})" if c.name else c.san for c in alternatives[:BOOK_ALTERNATIVES]
        )
        claims = [
            Claim(kind="legal_move", fen=mv.before.fen(), object=c.san)
            for c in alternatives[:BOOK_ALTERNATIVES]
        ]
        claims += _ground(" ".join(c.name for c in alternatives[:BOOK_ALTERNATIVES]), mv.before)
        out.append(("book", Sentence(f"책 밖의 수입니다. 책 수는 {listed}입니다.", claims)))
    else:
        out.append(("book", Sentence("책에서 이미 벗어난 국면입니다.")))
    return out


def _transposes(mv: _Move, played: list[chess.Move], from_start: bool) -> bool:
    """True when the position the move reaches is named but was not reached the book's way.

    Only decidable for a line played from the standard starting position: the comparison is
    against the whole move order of the rows that carry the name (`openings.find_rows`)."""
    arrived = lookup(mv.after)
    if arrived is None or not from_start:
        return False
    line = [move.uci() for move in (*played, mv.move)]
    rows = find_rows(arrived.eco, arrived.name)
    return all([move.uci() for move in moves_of(row)] != line for row in rows)


def _centre_and_development(mv: _Move, tension: bool = False) -> list[_Fact]:
    """The two things an opening move most often does at once (plan §5.3: merge them).

    A square counts when the piece did not already attack it from where it came from; the
    sentence names the pieces it now hits, or the squares it now controls."""
    if tension and mv.piece.piece_type == chess.PAWN:
        return []  # the tension sentence already named the pawn this one would attack
    gained = mv.after.attacks(mv.to_square) - mv.before.attacks(mv.move.from_square)
    central = [square for square in CENTRE if square in gained]
    if mv.piece.piece_type == chess.PAWN:
        named = sorted(gained)
    else:
        named = central
    develops = mv.piece.piece_type in (chess.KNIGHT, chess.BISHOP) and _at_home(
        mv.before, mv.move.from_square
    )
    if not central and not develops:
        return []

    enemy = [square for square in named if mv.after.color_at(square) == (not mv.color)]
    guarded = [square for square in named if mv.after.color_at(square) == mv.color]
    quiet = [square for square in named if square not in enemy and square not in guarded]
    claims = [_piece_claim(mv.after, mv.to_square)]
    claims += [
        _attack_claim(mv.after, mv.to_square, square) for square in named if square not in guarded
    ]
    claims += [_piece_claim(mv.after, square) for square in enemy]

    order = ORDINALS_KO[min(_developed(mv.after, mv.color), len(ORDINALS_KO)) - 1]
    develop_clause = f"{order} 번째 기물을 전개합니다" if develops else ""
    if enemy:
        targets = _join_ko([_named(mv.after, square) for square in enemy])
        head = f"{_eul(targets)} 공격"
    elif guarded:
        targets = _join_ko([_named(mv.after, square) for square in guarded])
        head = f"{_eul(targets)} 방어"
        claims += [
            Claim(
                kind="defends",
                fen=mv.after.fen(),
                subject=chess.square_name(mv.to_square),
                object=chess.square_name(square),
            )
            for square in guarded
        ]
    elif quiet:
        squares = _join_ko([chess.square_name(square) for square in quiet])
        head = f"{_eul(squares)} 통제"
    else:
        head = ""

    if develop_clause and head:
        text = f"{head}하며 {develop_clause}."
        kind: FactKind = "development"
    elif develop_clause:
        text = f"{_ro(_named(mv.after, mv.to_square))} {develop_clause}."
        kind = "development"
    else:
        subject = _named(mv.after, mv.to_square)
        text = f"{_ga(subject)} {head}합니다."
        kind = "center"
    return [(kind, Sentence(text, claims))]


def _queen_sortie(mv: _Move) -> list[_Fact]:
    """A queen out before the pieces are: the mistake the opening phase punishes most often."""
    if mv.piece.piece_type != chess.QUEEN or not _at_home(mv.before, mv.move.from_square):
        return []
    if _developed(mv.after, mv.color) > EARLY_QUEEN_PIECES:
        return []
    return [
        (
            "development",
            Sentence(
                f"퀸을 {_ro(chess.square_name(mv.to_square))} 일찍 꺼냅니다. "
                "상대가 전개하며 템포로 쫓기 쉽습니다.",
                [_piece_claim(mv.after, mv.to_square)],
            ),
        )
    ]


def _castling(mv: _Move) -> list[_Fact]:
    """Castling, or the king and rook moves that give the right away."""
    if mv.before.is_castling(mv.move):
        kingside = mv.before.is_kingside_castling(mv.move)
        king = chess.G1 if kingside else chess.C1
        rook = chess.F1 if kingside else chess.D1
        if mv.color == chess.BLACK:
            king, rook = king + 56, rook + 56
        side = "킹사이드" if kingside else "퀸사이드"
        return [
            (
                "castling",
                Sentence(
                    f"{side} 캐슬링으로 킹을 {chess.square_name(king)}로 옮기고 "
                    f"룩을 {chess.square_name(rook)}에 연결합니다.",
                    [_piece_claim(mv.after, king), _piece_claim(mv.after, rook)],
                ),
            )
        ]
    if mv.piece.piece_type not in (chess.KING, chess.ROOK):
        return []
    lost = [
        name
        for name, had, has in (
            (
                "킹사이드",
                mv.before.has_kingside_castling_rights(mv.color),
                mv.after.has_kingside_castling_rights(mv.color),
            ),
            (
                "퀸사이드",
                mv.before.has_queenside_castling_rights(mv.color),
                mv.after.has_queenside_castling_rights(mv.color),
            ),
        )
        if had and not has
    ]
    if not lost:
        return []
    what = PIECE_KO[mv.piece.piece_type]
    return [
        (
            "castling",
            Sentence(
                f"{_ga(what)} 움직여 {'·'.join(lost)} 캐슬링 권리를 잃습니다.",
                [_piece_claim(mv.after, mv.to_square)],
            ),
        )
    ]


def _fianchetto(mv: _Move) -> list[_Fact]:
    """A bishop on the long diagonal, or the pawn move that opens the square for it."""
    if mv.piece.piece_type == chess.BISHOP and mv.to_square in LONG_DIAGONAL:
        return [
            (
                "fianchetto",
                Sentence(
                    f"비숍을 {chess.square_name(mv.to_square)}의 긴 대각선에 놓습니다.",
                    [_piece_claim(mv.after, mv.to_square)],
                ),
            )
        ]
    home = FIANCHETTO_PAWNS.get(mv.to_square)
    if mv.piece.piece_type != chess.PAWN or home is None:
        return []
    if mv.after.piece_at(home) != chess.Piece(chess.BISHOP, mv.color):
        return []
    return [
        (
            "fianchetto",
            Sentence(
                f"{chess.square_name(mv.to_square)}로 {chess.square_name(home)} "
                "비숍의 피안케토를 준비합니다.",
                [_piece_claim(mv.after, mv.to_square), _piece_claim(mv.after, home)],
            ),
        )
    ]


def _tension(mv: _Move) -> list[_Fact]:
    """Pawn tension: the advance that makes it, the advance that walks into it, the capture
    that ends it (`reasoning._is_break`)."""
    if mv.piece.piece_type != chess.PAWN:
        return []
    if mv.before.is_capture(mv.move) and mv.before.piece_type_at(mv.to_square) == chess.PAWN:
        return [
            (
                "tension",
                Sentence(
                    f"{chess.square_name(mv.to_square)}에서 폰을 잡아 폰 긴장을 정리합니다.",
                    [_piece_claim(mv.after, mv.to_square)],
                ),
            )
        ]
    if not _is_break(mv.before, mv.move):
        return []
    hit = sorted(mv.after.attacks(mv.to_square) & mv.after.pieces(chess.PAWN, not mv.color))
    if hit:
        target = hit[0]
        return [
            (
                "tension",
                Sentence(
                    f"{_ro(chess.square_name(mv.to_square))} "
                    f"{_named(mv.after, target)}에 긴장을 만듭니다.",
                    [
                        _attack_claim(mv.after, mv.to_square, target),
                        _piece_claim(mv.after, target),
                    ],
                ),
            )
        ]
    step = 8 if mv.color == chess.WHITE else -8
    front = mv.to_square + step
    return [
        (
            "break",
            Sentence(
                f"{chess.square_name(mv.to_square)} 폰이 {_named(mv.after, front)}과 맞닿습니다.",
                [_piece_claim(mv.after, mv.to_square), _piece_claim(mv.after, front)],
            ),
        )
    ]


def _gambit(mv: _Move) -> list[_Fact]:
    """A pawn put where it can be taken and nothing defends it."""
    if mv.piece.piece_type != chess.PAWN or mv.before.is_capture(mv.move):
        return []
    attackers = sorted(mv.after.attackers(not mv.color, mv.to_square))
    if not attackers or mv.after.attackers(mv.color, mv.to_square):
        return []
    return [
        (
            "gambit",
            Sentence(
                f"{chess.square_name(mv.to_square)} 폰을 내어 주는 수입니다. "
                f"{_ga(_named(mv.after, attackers[0]))} 잡을 수 있습니다.",
                [
                    _piece_claim(mv.after, mv.to_square),
                    _piece_claim(mv.after, attackers[0]),
                    _attack_claim(mv.after, attackers[0], mv.to_square),
                ],
            ),
        )
    ]


def _motif_claims(mv: _Move, motif: Motif) -> list[Claim] | None:
    """Claims for every square `motifs.describe` names, or None when one of them is empty now
    (a motif whose pieces are no longer there cannot be stated in this position)."""
    squares = [motif.attacker, motif.mover, *motif.targets]
    if any(mv.after.piece_at(square) is None for square in squares):
        return None
    claims = [_piece_claim(mv.after, square) for square in dict.fromkeys(squares)]
    claims += [
        _attack_claim(mv.after, motif.attacker, target)
        for target in motif.targets[:1]
        if target in mv.after.attacks(motif.attacker)
    ]
    return claims


def _motifs(mv: _Move) -> list[_Fact]:
    """Pins, forks and discovered attacks, plus the plain 'this move attacks that piece'."""
    out: list[_Fact] = []
    for motif in detect(mv.before, mv.move):
        if motif.kind not in MOTIF_KINDS:
            continue
        claims = _motif_claims(mv, motif)
        if claims is None:
            continue
        out.append(("motif", Sentence(f"{describe(motif)}.", claims)))
        break
    if out:
        return out
    targets = [
        square
        for square in sorted(mv.after.attacks(mv.to_square) & mv.after.occupied_co[not mv.color])
        if (piece := mv.after.piece_type_at(square)) is not None
        and piece not in (chess.PAWN, chess.KING)
    ]
    if not targets:
        return []
    named = _join_ko([_named(mv.after, square) for square in targets[:2]])
    claims = [_piece_claim(mv.after, mv.to_square)]
    for square in targets[:2]:
        claims += [_attack_claim(mv.after, mv.to_square, square), _piece_claim(mv.after, square)]
    return [("motif", Sentence(f"{_eul(named)} 공격합니다.", claims))]


def _pins(board: chess.Board, attacker: chess.Square, victim_color: chess.Color) -> bool:
    """Whether the piece on `attacker` holds one of `victim_color`'s pieces in front of its own
    king or queen.

    Decided by x-ray: lift the piece it attacks off the board and see whether the king or the
    queen behind it comes into range. Cheap enough to run for every threat the opponent has."""
    piece = board.piece_at(attacker)
    if piece is None or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return False
    royal = board.pieces(chess.KING, victim_color) | board.pieces(chess.QUEEN, victim_color)
    seen = board.attacks(attacker) & royal
    probe = board.copy(stack=False)
    for target in board.attacks(attacker) & board.occupied_co[victim_color]:
        blocker = board.piece_at(target)
        if blocker is None or blocker.piece_type in (chess.KING, chess.QUEEN):
            continue
        probe.remove_piece_at(target)
        behind = [square for square in probe.attacks(attacker) & royal if square not in seen]
        probe.set_piece_at(target, blocker)
        if behind:
            return True
    return False


def _prophylaxis(mv: _Move) -> list[_Fact]:
    """A move that takes away something the opponent was about to do.

    The threats considered are the opponent's checks and pins in the position before the move
    (a null move gives them the turn); one of them counts as prevented when it is no longer
    legal afterwards, or when the square it was heading for is newly covered."""
    if mv.before.is_check():
        return []
    probe = mv.before.copy(stack=False)
    probe.push(chess.Move.null())
    for threat in probe.legal_moves:
        piece = probe.piece_at(threat.from_square)
        if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        if not probe.gives_check(threat):
            probe.push(threat)
            pinning = _pins(probe, threat.to_square, mv.color)
            probe.pop()
            if not pinning:
                continue
        square = threat.to_square
        blocked = threat not in mv.after.legal_moves
        covered = mv.after.is_attacked_by(mv.color, square) and not mv.before.is_attacked_by(
            mv.color, square
        )
        if not blocked and not covered:
            continue
        claims = [_piece_claim(mv.after, square)]
        if covered:
            guard = sorted(mv.after.attackers(mv.color, square))[0]
            claims.append(_attack_claim(mv.after, guard, square))
        return [("prophylaxis", Sentence(f"상대의 {probe.san(threat)}를 미리 막습니다.", claims))]
    return []


def _plan(mv: _Move) -> list[_Fact]:
    """A move that starts one of the structure's typical plans (`services.plans`)."""
    info = classify(mv.before)
    if info.key == "unclassified":
        return []
    side: kb.Side = "white" if mv.color == chess.WHITE else "black"
    mirror = kb.mirrored(info.key, mv.before)
    for spec in kb.plan_specs(info.key, side, mirror):
        if any(kb.hint_matches_move(hint, mv.before, mv.move) for hint in spec.hints):
            return [
                (
                    "plan",
                    Sentence(f"{info.name} 구조에서 '{spec.title}' 계획의 수입니다."),
                )
            ]
    return []


def _setup_name(name: str) -> str:
    """'런던 시스템 배치' — the knowledge base already calls some setups a 배치 themselves."""
    return name if name.endswith("배치") else f"{name} 배치"


def _setup(mv: _Move) -> list[_Fact]:
    """What the move did to a system setup: filled another square, or ruled one out."""
    changes = setups.advance(mv.before, mv.move)
    # The setup the move says most about: the one furthest along, and a setup it ruled out
    # before one it merely filled another square of (2.e3 ends the London; 2.Bf4 builds it).
    changes.sort(key=lambda change: (-change.done, change.kind != "blocked"))
    out: list[_Fact] = []
    for change in changes[:SETUP_FACTS]:
        if change.kind == "advance":
            out.append(
                (
                    "setup",
                    Sentence(
                        f"{_setup_name(change.name)} {change.done}/{change.total}.",
                        [_piece_claim(mv.after, mv.to_square)],
                    ),
                )
            )
        else:
            why = change.why or ""
            out.append(
                (
                    "setup",
                    Sentence(
                        f"이 수로 {_setup_name(change.name)}는 물 건너갑니다."
                        + (f" {why}." if why else ""),
                        _ground(why, mv.after),
                    ),
                )
            )
    return out


def _naturalness(mv: _Move, rating: int) -> tuple[float | None, list[_Fact]]:
    """How often a player of this rating plays this move (Maia, or its fallbacks)."""
    probs, _source = maia.move_probs(mv.before.fen(), rating)
    prob = probs.get(mv.san)
    if prob is None:
        return None, []
    return prob, [
        (
            "naturalness",
            Sentence(f"이 레이팅대({rating})에서 {_percent(prob)}가 두는 수입니다."),
        )
    ]


def _engine(mv: _Move, rating: int, depth: int | None) -> tuple[PlayCheckResponse, list[_Fact]]:
    """The shallow engine verdict on the move, summarised in one sentence.

    The reason `play_coach` wrote stays in the response for the panel; the sentence itself only
    states that the best move is legal here, which is the one board fact it names."""
    result = play_coach.check(
        PlayCheckRequest(fen_before=mv.before.fen(), san=mv.san, rating=rating, depth=depth)
    )
    verdict = CLASSIFICATION_KO.get(result.classification, result.classification)
    text = (
        f"엔진 판정은 {verdict}입니다. 승률 손실 {_percent(result.win_loss)}, "
        f"최선은 {result.best_san}."
    )
    claims = [Claim(kind="legal_move", fen=mv.before.fen(), object=result.best_san)]
    return result, [("engine", Sentence(text, claims))]


# ---------- one move ----------


def _assemble(facts: list[_Fact]) -> tuple[list[MoveFact], Assembly]:
    """Every detector's sentence as a fact, and the summary text over the ones that verified.

    Only sentences whose claims all hold reach the text, at most MAX_SENTENCES of them, in the
    order of PRIORITY (principle 1); the rest are still reported, marked unverified."""
    ordered = sorted(facts, key=lambda item: PRIORITY[item[0]])
    answer = assemble((sentence for _, sentence in ordered), limit=MAX_SENTENCES)
    out = [
        MoveFact(
            kind=kind,
            text=checked.sentence.text,
            claims=checked.sentence.claims,
            verified=checked.verified,
        )
        for (kind, _), checked in zip(ordered, answer.checked, strict=True)
    ]
    return out, answer


def facts(
    board: chess.Board,
    move: chess.Move,
    *,
    color: Color = "white",
    rating: int = 1500,
    naturalness: bool = False,
    played: list[chess.Move] | None = None,
    from_start: bool = False,
) -> list[MoveFact]:
    """Every deterministic fact about one move, without the engine. Raises ValueError for a
    move that is not legal here."""
    return _annotate_move(
        board,
        move,
        color=color,
        rating=rating,
        naturalness=naturalness,
        played=played or [],
        from_start=from_start,
        engine=None,
    ).facts


def _annotate_move(
    board: chess.Board,
    move: chess.Move,
    *,
    color: Color,
    rating: int,
    naturalness: bool,
    played: list[chess.Move],
    from_start: bool,
    engine: tuple[EngineMode, int | None] | None,
) -> MoveAnnotation:
    """One move: the facts, the sentences that survived the verifier, and the extras the page
    shows around them (book alternatives, engine verdict, naturalness)."""
    if not board.is_legal(move):
        raise ValueError(f"{ILLEGAL_MOVE}: {move.uci()}")
    san = board.san(move)
    after = board.copy(stack=False)
    after.push(move)
    piece = board.piece_at(move.from_square)
    assert piece is not None  # a legal move always has a piece to move
    mv = _Move(board, after, move, san, board.ply() + 1, board.turn, piece)

    book_moves = [candidate for candidate, _opening in next_moves(board)]
    arrived = lookup(after)
    here = lookup(board)
    in_book = move in book_moves or arrived is not None
    alternatives = [] if in_book else opening_guide.candidates(board, color)[:BOOK_ALTERNATIVES]
    transposition = _transposes(mv, played, from_start)

    tension = _tension(mv)
    collected: list[_Fact] = [
        *_book_facts(mv, in_book, alternatives),
        *_motifs(mv),
        *tension,
        *_gambit(mv),
        *_centre_and_development(mv, any(kind == "tension" for kind, _ in tension)),
        *_queen_sortie(mv),
        *_castling(mv),
        *_fianchetto(mv),
        *_prophylaxis(mv),
        *_plan(mv),
        *_setup(mv),
    ]
    if transposition:
        collected.append(("transposition", Sentence("다른 수순으로 같은 국면에 합류합니다.")))

    probability: float | None = None
    if naturalness:
        probability, natural_facts = _naturalness(mv, rating)
        collected += natural_facts

    verdict: PlayCheckResponse | None = None
    if engine is not None:
        mode, depth = engine
        if mode == "always" or (mode == "off_book" and not in_book):
            verdict, engine_facts = _engine(mv, rating, depth)
            collected += engine_facts

    move_facts, answer = _assemble(collected)
    return MoveAnnotation(
        ply=mv.ply,
        label=move_label(mv.ply, san),
        san=san,
        uci=move.uci(),
        fen_before=board.fen(),
        fen_after=after.fen(),
        in_book=in_book,
        name_before=name_ko(here.name) if here is not None else None,
        name_after=name_ko(arrived.name) if arrived is not None else None,
        name_after_en=arrived.name if arrived is not None else None,
        transposition=transposition,
        book_alternatives=alternatives,
        facts=move_facts,
        text=answer.text,
        engine=verdict,
        naturalness=round(probability, maia.PRECISION) if probability is not None else None,
        verified=answer.verified,
        verified_claims=answer.verified_claims,
        total_claims=answer.total_claims,
    )


def annotate(req: AnnotateRequest) -> list[MoveAnnotation]:
    """Annotate a whole line, one entry per move.

    Raises ValueError for a start position that breaks the rules or a move that cannot be
    played. The engine is asked for at most ANNOTATE_ENGINE_CAP moves; past that the move
    still gets its facts and the page can ask for the verdict on its own."""
    board = _board(req.start_fen)
    from_start = board.board_fen() == chess.Board().board_fen() and board.ply() == 0
    played: list[chess.Move] = []
    out: list[MoveAnnotation] = []
    engine_calls = 0
    for san in req.moves_san:
        try:
            move = board.parse_san(san)
        except ValueError as exc:
            raise ValueError(f"{ILLEGAL_MOVE}: {san}") from exc
        budget = engine_calls < ANNOTATE_ENGINE_CAP
        annotation = _annotate_move(
            board,
            move,
            color=req.color,
            rating=req.rating,
            naturalness=req.naturalness,
            played=played,
            from_start=from_start,
            engine=(req.engine, req.depth) if budget and req.engine != "off" else None,
        )
        if annotation.engine is not None:
            engine_calls += 1
        out.append(annotation)
        played.append(move)
        board.push(move)
    return out
