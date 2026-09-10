"""Setup knowledge base: how far a system opening has come and what put it out of reach.

Positions are written as move lists so the expectations read like the lines they come from.
"""

from __future__ import annotations

import chess
import pytest

from chess_tutor.schemas import SetupStatus
from chess_tutor.services import setups

LONDON_COMPLETE = "d4 d5 Bf4 Nf6 e3 e6 Nf3 Be7 Bd3 O-O Nbd2 c5 c3"
"""1.d4 d5 2.Bf4 Nf6 3.e3 e6 4.Nf3 Be7 5.Bd3 O-O 6.Nbd2 c5 7.c3: all seven squares filled."""

KID_LINE = "d4 Nf6 c4 g6 Nc3 Bg7"
ONE_KNIGHT_FEN = "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/R1BQKBNR w KQkq - 0 3"
"""A queen's pawn position where White has a single knight, so the London cannot fill both
knight squares."""


def board(moves: str = "") -> chess.Board:
    b = chess.Board()
    for san in moves.split():
        b.push_san(san)
    return b


def status_of(setup_id: str, moves: str = "") -> SetupStatus:
    spec = setups.find(setup_id)
    assert spec is not None, setup_id
    return setups.status(spec, board(moves))


# ---------- knowledge base ----------


def test_knowledge_base_is_eight_white_and_six_black_setups() -> None:
    assert len(setups.SETUPS) == 14
    assert len({spec.id for spec in setups.SETUPS}) == 14
    assert sum(1 for spec in setups.SETUPS if spec.side == "white") == 8
    assert sum(1 for spec in setups.SETUPS if spec.side == "black") == 6
    for spec in setups.SETUPS:
        assert 2 <= len(spec.plans) <= 3, spec.id
        assert 4 <= len(spec.targets) <= 7, spec.id
        assert len({square for square, _ in spec.targets}) == len(spec.targets), spec.id


def test_prose_names_squares_and_pieces_but_never_a_colour() -> None:
    """The rule plans.py follows: a colour word would make a mirrored entry a lie."""
    for spec in setups.SETUPS:
        for text in (spec.name, spec.against or "", *spec.plans):
            assert "백" not in text and "흑" not in text, (spec.id, text)


# ---------- status ----------


def test_every_setup_is_possible_in_the_starting_position() -> None:
    all_setups = setups.status_all(chess.Board())
    assert len(all_setups) == 14
    assert {s.status for s in all_setups} == {"possible"}
    assert all(not s.done and s.blocked_by is None for s in all_setups)
    london = next(s for s in all_setups if s.id == "london")
    assert london.remaining == ["d4", "Bf4", "e3", "Nf3", "c3", "Nd2", "Bd3"]
    assert london.plans and london.typical_against == "…d5 계열"


def test_london_advances_then_completes() -> None:
    started = status_of("london", "d4 d5 Bf4")
    assert started.status == "in_progress"
    assert started.done == ["d4", "Bf4"]
    assert started.remaining == ["e3", "Nf3", "c3", "Nd2", "Bd3"]
    assert started.blocked_by is None

    done = status_of("london", LONDON_COMPLETE)
    assert done.status == "completed"
    assert len(done.done) == 7 and done.remaining == []
    assert done.blocked_by is None


def test_e3_before_bf4_shuts_the_bishop_in() -> None:
    blocked = status_of("london", "d4 d5 e3")
    assert blocked.status == "blocked"
    assert blocked.done == ["d4", "e3"]
    assert blocked.blocked_by == "e3를 먼저 두어 c1 비숍이 갇혔습니다"
    # The Torre keeps the same bishop outside the chain, so the same move ends it too.
    assert status_of("torre", "d4 d5 e3").blocked_by == blocked.blocked_by
    # The Colle leaves that bishop at home on purpose: e3 is one of its own squares.
    assert status_of("colle_koltanowski", "d4 d5 e3").status == "in_progress"


def test_a_bishop_already_outside_the_chain_is_not_shut_in() -> None:
    """The order rule only fires while the piece is still on its home square."""
    after = status_of("london", "d4 d5 Bf4 Nf6 e3")
    assert after.status == "in_progress" and "Bf4" in after.done


def test_kid_progresses_for_black() -> None:
    kid = status_of("kid", KID_LINE)
    assert kid.side == "black" and kid.status == "in_progress"
    assert kid.done == ["Nf6", "g6", "Bg7"]
    assert kid.remaining == ["d6", "O-O"]


def test_hedgehog_is_out_once_the_e_pawn_passes_e6() -> None:
    assert status_of("hedgehog").status == "possible"
    blocked = status_of("hedgehog", "e4 e5")
    assert blocked.status == "blocked"
    assert blocked.blocked_by == "e파일 폰이 이미 지나갔거나 없습니다"
    # ...e6 first keeps it alive.
    assert status_of("hedgehog", "e4 e6").status == "in_progress"


def test_a_pawn_target_blocked_by_a_piece_says_which_square() -> None:
    """A knight on f6 stands where the Stonewall Dutch wants its f-pawn to pass."""
    blocked = status_of("stonewall_dutch", "d4 Nf6")
    assert blocked.status == "blocked"
    assert blocked.blocked_by == "f6에 기물이 있어 f5까지 폰이 지나갈 수 없습니다"


def test_castling_target_needs_the_castling_right() -> None:
    assert status_of("kia", "e4 e5").status == "in_progress"
    blocked = status_of("kia", "e4 e5 Ke2")
    assert blocked.status == "blocked"
    assert blocked.blocked_by == "캐슬링 권리를 잃었습니다"
    assert "O-O" in blocked.remaining


def test_two_targets_of_one_type_need_two_pieces() -> None:
    """A single knight cannot stand on f3 and d2 at the same time."""
    spec = setups.find("london")
    assert spec is not None
    single = setups.status(spec, chess.Board(ONE_KNIGHT_FEN))
    assert single.status == "blocked"
    assert single.blocked_by is not None and "나이트가 없습니다" in single.blocked_by
    # With both knights on the board the same position is fine.
    assert setups.status(spec, board("d4 d5")).status == "in_progress"


def test_status_all_orders_side_to_move_first_then_progress() -> None:
    ordered = setups.status_all(board("d4 d5 Bf4"))
    sides = [s.side for s in ordered]
    assert sides.index("white") > max(i for i, s in enumerate(sides) if s == "black")
    whites = [s for s in ordered if s.side == "white"]
    assert whites[0].id == "london" and whites[0].status == "in_progress"
    rank = {"completed": 0, "in_progress": 1, "possible": 2, "blocked": 3}
    assert [rank[s.status] for s in whites] == sorted(rank[s.status] for s in whites)
    finished = setups.status_all(board(LONDON_COMPLETE))
    assert next(s for s in finished if s.side == "white").id == "london"


# ---------- advance ----------


def test_advance_reports_the_square_filled_and_the_setup_lost() -> None:
    before = board("d4 d5")
    filled = setups.advance(before, before.parse_san("Bf4"))
    london = next(c for c in filled if c.setup_id == "london")
    assert london.kind == "advance" and (london.done, london.total) == (2, 7)
    # The same move takes f4 away from the Stonewall Attack.
    stonewall = next(c for c in filled if c.setup_id == "stonewall_attack")
    assert stonewall.kind == "blocked" and stonewall.why == "f4에 다른 기물이 있습니다"

    shut_in = setups.advance(before, before.parse_san("e3"))
    lost = next(c for c in shut_in if c.setup_id == "london")
    assert lost.kind == "blocked" and lost.why == "e3를 먼저 두어 c1 비숍이 갇혔습니다"
    assert any(c.setup_id == "colle_koltanowski" and c.kind == "advance" for c in shut_in)


def test_advance_only_talks_about_the_side_that_moved() -> None:
    before = board("d4 Nf6 c4")
    changes = setups.advance(before, before.parse_san("g6"))
    assert {c.setup_id for c in changes} == {"kid", "pirc"}
    assert all(c.kind == "advance" for c in changes)


def test_advance_refuses_an_illegal_move() -> None:
    with pytest.raises(ValueError, match="둘 수 없는 수"):
        setups.advance(chess.Board(), chess.Move.from_uci("e2e5"))
