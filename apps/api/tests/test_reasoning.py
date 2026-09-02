from collections.abc import Callable

import chess
import pytest

from chess_tutor.engine import find_stockfish
from chess_tutor.schemas import EngineLine, FeatureDiffRow, Score, StructureInfo
from chess_tutor.services import reasoning, verbalize
from chess_tutor.verify import verify_all

# Mockup review position: Black to move, 20...Nxd5 (best) vs 20...exd5.
REVIEW = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
# Mockup strategy tab: Sicilian hedgehog, Black to play 14...Qb8.
HEDGEHOG = "2r2rk1/pbqnbppp/1p1ppn2/8/2PNP3/2N1BB2/PP1Q1PPP/2RR2K1 b - - 4 14"


def _line(board: chess.Board, sans: str) -> list[chess.Move]:
    b = board.copy()
    out = []
    for san in sans.split():
        m = b.parse_san(san)
        out.append(m)
        b.push(m)
    return out


def _engine_line(board: chess.Board, sans: str, cp: int, rank: int = 1) -> EngineLine:
    moves = _line(board, sans)
    return EngineLine(
        rank=rank, score=Score(cp=cp), pv=sans.split(), pv_uci=[m.uci() for m in moves]
    )


# ---------- divergence ----------


def test_divergence_at_first_move() -> None:
    board = chess.Board(REVIEW)
    a = _line(board, "Nxd5 exd5 exd5 Qd3")
    b = _line(board, "exd5 exd5 Qd6 Bf4")
    idx, fen = reasoning.divergence(board, a, b)
    assert idx == 0
    after = board.copy()
    after.push_san("Nxd5")
    assert fen == after.fen()


def test_divergence_later_and_identical_lines() -> None:
    board = chess.Board(REVIEW)
    a = _line(board, "Nxd5 exd5 exd5 Qd3")
    b = _line(board, "Nxd5 exd5 exd5 Bd4")
    idx, fen = reasoning.divergence(board, a, b)
    assert idx == 3
    assert fen == reasoning._play(board, a[:4]).fen()
    assert reasoning.divergence(board, a, list(a)) == (None, None)
    # a prefix of the other line: the index is the shorter length
    idx, fen = reasoning.divergence(board, a[:2], a)
    assert idx == 2
    assert fen == reasoning._play(board, a[:2]).fen()


# ---------- pawn facts used by the fallback rows ----------


def test_pawn_structure_facts_after_each_line() -> None:
    board = chess.Board(REVIEW)
    end_a = reasoning._play(board, _line(board, "Nxd5 exd5 exd5 Qd3"))
    end_b = reasoning._play(board, _line(board, "exd5 exd5 Qd6 Bf4"))
    assert reasoning.passed_pawns(end_a, chess.BLACK) == [chess.D5]
    assert reasoning.passed_pawns(end_a, chess.WHITE) == []
    assert reasoning.isolated_pawns(end_a, chess.BLACK) == [chess.D5]
    assert reasoning.passed_pawns(end_b, chess.WHITE) == [chess.D5]
    assert reasoning.isolated_pawns(end_b, chess.WHITE) == [chess.D5]
    assert reasoning.material(end_a, chess.BLACK) == 1  # N for N, then P for P, then a pawn
    assert reasoning.material(end_b, chess.BLACK) == 2  # knight for a pawn in that line


# ---------- compare_moves ----------


def test_compare_moves_on_the_mockup_position() -> None:
    board = chess.Board(REVIEW)
    pv_a = _line(board, "Nxd5 exd5 exd5 Qd3")
    pv_b = _line(board, "exd5 exd5 Qd6 Bf4")
    cmp = reasoning.compare_moves(
        board, "Nxd5", "exd5", pv_a, pv_b, "black", Score(cp=30), Score(cp=60)
    )
    assert cmp.a_san == "Nxd5" and cmp.b_san == "exd5"
    assert cmp.divergence_ply == 0
    after = board.copy()
    after.push_san("Nxd5")
    assert cmp.divergence_fen == after.fen()
    assert cmp.rows, "either features.feature_diff or the fallback rows must produce rows"
    assert all(r.delta is None or abs(r.delta) > 0 for r in cmp.rows)
    assert all(r.a != r.b or r.delta for r in cmp.rows)
    assert cmp.summary.endswith(".")
    assert "—" not in cmp.summary
    # the heading is '왜 Nxd5가 exd5보다 나은가', so the leading clause is about Nxd5 and the
    # engine verdict is always there to back it (reasoning-R2)
    assert cmp.summary.startswith("Nxd5가 ")
    assert cmp.summary.endswith("엔진 평가는 Nxd5 +0.3, exd5 +0.6입니다.")
    quoted = [r for r in cmp.rows if r.feature in cmp.summary]
    assert quoted and all((r.delta or 0) > 0 for r in quoted)


def test_compare_moves_identical_lines_have_no_rows() -> None:
    board = chess.Board(REVIEW)
    pv = _line(board, "Nxd5 exd5 exd5 Qd3")
    cmp = reasoning.compare_moves(
        board, "Nxd5", "Nxd5", pv, list(pv), "black", Score(cp=30), Score(cp=30)
    )
    assert cmp.rows == []
    assert cmp.divergence_ply is None
    assert "+0.3" in cmp.summary


# ---------- explain_alternative ----------


def test_explain_alternative_settles_the_exchange_instead_of_narrating_it() -> None:
    board = chess.Board(REVIEW)
    best, claims = reasoning.explain_alternative(
        board, "Nxd5", _line(board, "Nxd5 exd5 exd5 Qd3"), Score(cp=30), Score(cp=30), "black"
    )
    assert best.startswith("d5 나이트를 나이트로 잡습니다")
    assert "e7 비숍을 노리던 위협이 사라집니다" in best
    # the recapture is not narrated on its own (that reads as hanging the piece); the whole
    # exchange is settled in one clause instead
    assert "백이 exd5로 받아도 흑이 exd5로 되잡습니다" in best
    assert "교환 뒤 흑이 폰 1개만큼 앞섭니다" in best
    assert "d5 나이트를 폰으로 잡습니다" not in best
    # the template already prints the '엔진 최선' tag and the evaluation
    assert "엔진 최선 수입니다" not in best
    assert verbalize.unclaimed_squares(best, claims) == 0
    assert all(v.holds for v in verify_all(claims))

    worse, claims = reasoning.explain_alternative(
        board, "exd5", _line(board, "exd5 exd5 Qd6 Bf4"), Score(cp=60), Score(cp=30), "black"
    )
    assert worse.startswith("d5 나이트를 폰으로 잡습니다")
    assert "백이 exd5로 되잡습니다" in worse
    assert verbalize.unclaimed_squares(worse, claims) == 0
    assert all(v.holds for v in verify_all(claims))
    assert "—" not in best + worse


def test_explain_alternative_mentions_a_fork_and_claims_every_square() -> None:
    board = chess.Board("5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21")
    text, claims = reasoning.explain_alternative(
        board, "Nxf6+", _line(board, "Nxf6+ Bxf6"), Score(cp=500), Score(cp=500), "white"
    )
    assert "체크입니다" in text
    assert "f6 나이트가 체크와 함께 d7 퀸을 노립니다" in text  # the king is the check itself
    assert "d1 룩의 길이 열려 d7 퀸을 겨냥합니다" in text
    assert verbalize.unclaimed_squares(text, claims) == 0
    assert all(v.holds for v in verify_all(claims))


def test_explain_alternative_never_prints_a_pawn_difference_against_a_mate() -> None:
    """Score.as_pawns() calls any mate ±100, so a delta would read '최선보다 105.9 뒤집니다'."""
    board = chess.Board("5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21")
    text, _ = reasoning.explain_alternative(
        board, "Nxf6+", _line(board, "Nxf6+ Bxf6"), Score(cp=-590), Score(mate=2), "white"
    )
    assert text.endswith("메이트를 놓칩니다.")
    assert "105" not in text and "뒤집니다" not in text
    mating, _ = reasoning.explain_alternative(
        board, "Nxf6+", _line(board, "Nxf6+ Bxf6"), Score(mate=2), Score(mate=2), "white"
    )
    assert "메이트를 놓칩니다" not in mating


def test_basic_rows_are_the_fallback_when_features_is_absent() -> None:
    board = chess.Board(REVIEW)
    end_a = reasoning._play(board, _line(board, "Nxd5 exd5 exd5 Qd3"))
    end_b = reasoning._play(board, _line(board, "exd5 exd5 Qd6 Bf4"))
    rows = reasoning._basic_rows(end_a, end_b, chess.BLACK)
    passed = next(r for r in rows if r.feature == "통과폰")
    assert passed.a == "흑 d5 / 백 없음"
    assert passed.b == "흑 없음 / 백 d5"
    assert passed.delta == 2.0
    assert {r.feature for r in rows} == {"물질", "통과폰", "고립폰", "겹폰", "기물 활동"}


# ---------- extract_plan ----------


def test_extract_plan_reads_destinations_break_and_exchange() -> None:
    board = chess.Board(HEDGEHOG)
    sketch = reasoning.extract_plan(board, _line(board, "Qb8 Qe1 Rfd8 Bg5 Ne5 Be2 d5"))
    assert sketch.side == "black"
    assert sketch.piece_destinations == {"Qc7": "b8", "Rf8": "d8", "Nd7": "e5"}
    assert sketch.pawn_breaks == ["d5"]
    assert sketch.exchanges == []
    assert sketch.plies == 7
    assert "...d5 브레이크" in sketch.summary
    assert "나이트를 e5로" in sketch.summary
    assert "룩을 d8로" in sketch.summary

    review = chess.Board(REVIEW)
    sketch = reasoning.extract_plan(review, _line(review, "Nxd5 exd5 exd5 Qd3"))
    assert sketch.exchanges == ["Nxd5 exd5"]
    assert "나이트 교환" in sketch.summary
    assert sketch.piece_destinations == {"Nf6": "d5"}


def test_extract_plan_castling_and_unequal_exchange() -> None:
    board = chess.Board()
    line = _line(board, "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Nxd5 Nxf7 Kxf7")
    sketch = reasoning.extract_plan(board, line)
    assert sketch.side == "white"
    assert sketch.exchanges == ["exd5 Nxd5", "Nxf7 Kxf7"]
    assert "나이트와 폰 교환" in sketch.summary  # White gave a knight for the f7 pawn
    assert "e4 브레이크" not in sketch.summary  # e4 attacks nothing at that moment

    castle = reasoning.extract_plan(board, _line(board, "e4 e5 Nf3 Nc6 Bc4 Bc5 O-O"))
    assert castle.king_moves == ["O-O"]
    assert "캐슬링" in castle.summary


# ---------- counterfactual ----------


def _fake_analyse(
    premature_cp: int, prepared_cp: int
) -> Callable[[str, int, int], list[EngineLine]]:
    """Scores from White's view: a black pawn on d5 is worth premature_cp when it arrives on
    move 15 or earlier and prepared_cp afterwards; everything else is +10."""

    def analyse(fen: str, depth: int, multipv: int) -> list[EngineLine]:
        board = chess.Board(fen)
        pawn = board.piece_at(chess.D5)
        if pawn == chess.Piece(chess.PAWN, chess.BLACK):
            cp = premature_cp if board.fullmove_number <= 15 else prepared_cp
        else:
            cp = 10
        moves = sorted(board.legal_moves, key=lambda m: m.uci())[:multipv]
        return [
            EngineLine(rank=i + 1, score=Score(cp=cp), pv=[board.san(m)], pv_uci=[m.uci()])
            for i, m in enumerate(moves)
        ]

    return analyse


def test_counterfactual_says_too_early_when_preparing_scores_better() -> None:
    board = chess.Board(HEDGEHOG)
    cf = reasoning.counterfactual(board, "d5", _fake_analyse(premature_cp=80, prepared_cp=10))
    assert cf.question == "지금 14...d5를 두면?"
    assert cf.line[0] == "d5"
    assert cf.eval == Score(cp=80)
    assert cf.verdict.startswith("아직 이르다: 준비 후가 +0.7 낫다")
    assert "d5" in cf.verdict  # the delayed line is quoted


def test_counterfactual_says_now_when_delaying_loses() -> None:
    board = chess.Board(HEDGEHOG)
    cf = reasoning.counterfactual(board, "d5", _fake_analyse(premature_cp=10, prepared_cp=80))
    assert cf.verdict.startswith("지금이 적기: 미루면 -0.7")


def test_counterfactual_says_meate_in_words_not_in_pawns() -> None:
    """A mate is ±100 pawns, so the numeric verdict would read '미루면 -105.9'."""
    board = chess.Board(HEDGEHOG)

    def analyse(fen: str, depth: int, multipv: int) -> list[EngineLine]:
        b = chess.Board(fen)
        on_d5 = b.piece_at(chess.D5) == chess.Piece(chess.PAWN, chess.BLACK)
        mate = -2 if on_d5 and b.fullmove_number <= 15 else None
        moves = sorted(b.legal_moves, key=lambda m: m.uci())[:multipv]
        return [
            EngineLine(
                rank=i + 1,
                score=Score(cp=None if mate else 10, mate=mate),
                pv=[b.san(m)],
                pv_uci=[m.uci()],
            )
            for i, m in enumerate(moves)
        ]

    cf = reasoning.counterfactual(board, "d5", analyse)
    assert cf.verdict.startswith("지금이 적기: 미루면 메이트를 놓칩니다")
    assert "10" not in cf.verdict.split("(")[0]


def test_waiting_move_falls_back_only_to_a_move_that_keeps_the_eval() -> None:
    board = chess.Board(HEDGEHOG)
    target = board.parse_san("d5")

    def only_pawn_lines(fen: str, depth: int, multipv: int) -> list[EngineLine]:
        """Every candidate is a pawn move, so _waiting_move must use its own fallback; every
        position it then evaluates is a disaster."""
        b = chess.Board(fen)
        if b.fen() != board.fen():
            return [EngineLine(rank=1, score=Score(cp=900), pv=[], pv_uci=[])]
        moves = [m for m in b.legal_moves if b.piece_type_at(m.from_square) == chess.PAWN]
        return [
            EngineLine(rank=i + 1, score=Score(cp=0), pv=[b.san(m)], pv_uci=[m.uci()])
            for i, m in enumerate(moves[:multipv])
        ]

    assert reasoning._waiting_move(board, target, only_pawn_lines, 1, 5) is None


def test_waiting_move_is_a_quiet_non_pawn_move() -> None:
    board = chess.Board(HEDGEHOG)
    target = board.parse_san("d5")
    waiting = reasoning._waiting_move(board, target, _fake_analyse(80, 10), 1, 5)
    assert waiting is not None and waiting != target
    assert board.piece_type_at(waiting.from_square) != chess.PAWN
    assert not board.is_capture(waiting) and not board.gives_check(waiting)


@pytest.mark.skipif(find_stockfish() is None, reason="stockfish not installed")
def test_counterfactual_with_stockfish() -> None:
    from chess_tutor.engine import Engine

    board = chess.Board(HEDGEHOG)
    with Engine() as engine:

        def analyse(fen: str, depth: int, multipv: int) -> list[EngineLine]:
            b = chess.Board(fen)
            out = []
            for ln in engine.analyse(b, depth=depth, multipv=multipv):
                sans = reasoning._sans(b, ln.pv)
                out.append(
                    EngineLine(
                        rank=ln.rank,
                        score=Score(cp=ln.score_cp, mate=ln.mate),
                        pv=sans,
                        pv_uci=[m.uci() for m in ln.pv[: len(sans)]],
                    )
                )
            return out

        cf = reasoning.counterfactual(board, "d5", analyse, depth=8, multipv=3)
    assert cf.line[0] == "d5"
    assert cf.verdict.split(":")[0] in {"지금이 적기", "아직 이르다", "시점 차이 없음"}


# ---------- strategy_view ----------


def test_strategy_view_on_the_hedgehog_position() -> None:
    board = chess.Board(HEDGEHOG)
    line = _engine_line(board, "Qb8 Qe1 Rfd8 Bg5 Ne5 Be2 d5", cp=20)
    info = StructureInfo(
        key="hedgehog", name="헤지호그", confidence=0.92, defining_pawns=["a7", "b6", "d6", "e6"]
    )
    view = reasoning.strategy_view(
        [board],
        0,
        [line],
        "Qb8",
        "best",
        "black",
        analyse=_fake_analyse(80, 10),
        record={"games": 12, "win_rate": 0.33},
        structure=info,
    )
    assert view.structure is not None and view.structure.key == "hedgehog"
    black = [p for p in view.plans if p.side == "black"]
    white = [p for p in view.plans if p.side == "white"]
    assert black and white
    assert view.plans[0].side == "black"  # the user's side comes first
    assert next(p for p in black if p.title == "...d5 브레이크").status == "pv_match"
    assert view.your_move is not None
    assert view.your_move.plan_match is True
    assert "세 번째 줄 뒤 기물 정렬" in view.your_move.note
    assert "엔진 1순위" in view.your_move.note
    assert view.counterfactual is not None
    assert view.counterfactual.question == "지금 14...d5를 두면?"
    assert view.record == {"games": 12, "win_rate": 0.33}
    assert isinstance(view.timeline, list)
    assert isinstance(view.features, list)


def test_strategy_view_degrades_without_a_structure() -> None:
    boards = [chess.Board()]
    for san in ("e4", "c5", "Nf3"):
        nxt = boards[-1].copy()
        nxt.push_san(san)
        boards.append(nxt)
    view = reasoning.strategy_view(boards, 3, [], "d6", "book", "black")
    assert view.plans == []
    assert view.structure is not None and view.structure.key == "unclassified"
    assert view.your_move is not None
    assert view.your_move.san == "d6"
    assert view.counterfactual is None
    assert view.your_move.note == "2...d6: 이 국면의 구조에는 등록된 계획이 없습니다"


def test_strategy_note_particles_follow_the_move() -> None:
    board = chess.Board(HEDGEHOG)
    info = StructureInfo(key="hedgehog", name="헤지호그", confidence=0.9)
    view = reasoning.strategy_view(
        [board], 0, [], "Qb8", "good", "black", structure=info, record=None
    )
    assert view.your_move is not None
    # 8 = 팔 carries a final consonant, so the topic particle is 은, never a hard-coded 는
    assert "14...Qb8은" in view.your_move.note
    assert "14...Qb8는" not in view.your_move.note


def test_strategy_view_played_moves_come_from_the_boards() -> None:
    start = chess.Board("5rk1/p3bppp/1pq1pn2/8/4P3/2N1B3/PP2QPPP/3R2K1 w - - 3 20")
    after = start.copy()
    after.push_san("Nd5")
    boards = [start, after]
    info = StructureInfo(key="hedgehog", name="헤지호그", confidence=0.8)
    view = reasoning.strategy_view(boards, 1, [], "Qd7", "blunder", "black", structure=info)
    nd5 = next(p for p in view.plans if p.title == "Nd5 점프 또는 희생")
    assert nd5.status == "executed"


def test_comparison_summary_never_leads_with_the_worse_move() -> None:
    """The panel heading is '왜 a가 b보다 나은가', so the sentence under it may not praise b."""
    rows = [
        FeatureDiffRow(feature="기물 활동", a="흑 29수", b="흑 21수", delta=-1.8),
        FeatureDiffRow(feature="통과폰", a="흑 d5", b="백 d5", delta=0.5),
    ]
    summary = reasoning._comparison_summary("Bxf6", "Bxd7+", rows, Score(cp=550), Score(cp=250))
    assert summary.startswith("Bxf6이 통과폰에서 앞섭니다.")  # 6 = 육
    assert summary.endswith("엔진 평가는 Bxf6 +5.5, Bxd7+ +2.5입니다.")

    # every feature favours the played move: concede it and end on the evaluation
    only_theirs = [rows[0]]
    summary = reasoning._comparison_summary(
        "Bxf6", "Bxd7+", only_theirs, Score(cp=550), Score(cp=250)
    )
    assert summary.startswith("기물 활동만 놓고 보면 Bxd7+이 앞서지만,")
    assert summary.endswith("엔진 평가는 Bxf6 +5.5, Bxd7+ +2.5입니다.")
    assert not summary.startswith("Bxd7+")


def test_compare_moves_stops_both_lines_at_the_same_ply_and_skips_mate_rows() -> None:
    """A three-ply mating line and a six-ply quiet line are not a like-for-like diff."""
    board = chess.Board("4kb1r/p2n1ppp/4q3/4p1B1/4P3/1Q6/PPP2PPP/2KR4 w k - 0 16")
    mate = _line(board, "Qb8+ Nxb8 Rd8#")
    quiet = _line(board, "Qc3 f6 Qc8+ Ke7 Be3 g5")
    cmp = reasoning.compare_moves(
        board, "Qb8+", "Qc3", mate, quiet, "white", Score(mate=2), Score(cp=330)
    )
    assert cmp.rows == []  # no material or mobility count off a checkmate
    assert cmp.summary == "Qb8+이 메이트로 이어집니다. 엔진 평가는 Qb8+ #2, Qc3 +3.3입니다."
    # without a mate the lines are still truncated to the shorter one
    short = reasoning.compare_moves(
        board, "Qc3", "Qb7", quiet[:2], quiet[:6], "white", Score(cp=330), Score(cp=280)
    )
    assert short.divergence_ply is not None
