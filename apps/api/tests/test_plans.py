import chess

from chess_tutor.services import plans

# Mockup strategy tab: Sicilian hedgehog, Black to play 14...Qb8.
HEDGEHOG = "2r2rk1/pbqnbppp/1p1ppn2/8/2PNP3/2N1BB2/PP1Q1PPP/2RR2K1 b - - 4 14"
# Mockup review position (20...?) and the position one ply earlier, before 20.Nd5.
REVIEW = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
BEFORE_ND5 = "5rk1/p3bppp/1pq1pn2/8/4P3/2N1B3/PP2QPPP/3R2K1 w - - 3 20"


def _line(board: chess.Board, sans: str) -> list[chess.Move]:
    b = board.copy()
    out = []
    for san in sans.split():
        m = b.parse_san(san)
        out.append(m)
        b.push(m)
    return out


def _by_title(found: list, title: str):
    return next(p for p in found if p.title == title)


# ---------- knowledge base shape ----------


def test_every_structure_has_three_plans_per_side_with_parseable_hints() -> None:
    expected = {
        "hedgehog", "iqp", "hanging_pawns", "carlsbad", "maroczy", "french_chain", "kid",
        "benoni", "open_center", "slav_caro", "stonewall", "scheveningen", "closed_center",
        "boleslavsky_hole",
    }  # fmt: skip
    assert expected <= set(plans.PLANS)
    for key, sides in plans.PLANS.items():
        for side in ("white", "black"):
            specs = sides[side]
            assert len(specs) >= 3, (key, side)
            for spec in specs:
                assert spec.title and spec.condition, (key, spec)
                assert "—" not in spec.title + spec.condition, (key, spec.title)
                for hint in spec.hints:
                    assert plans.parse_hint(hint), (key, spec.title, hint)


def test_parse_hint_variants() -> None:
    steps = plans.parse_hint("minority attack b4-b5")
    assert [(s.piece_type, chess.square_name(s.to_square)) for s in steps] == [
        (chess.PAWN, "b4"),
        (chess.PAWN, "b5"),
    ]
    (knight,) = plans.parse_hint("...Nd5")
    assert (knight.piece_type, knight.to_square) == (chess.KNIGHT, chess.D5)
    (bishop,) = plans.parse_hint("Bxf6")
    assert (bishop.piece_type, bishop.to_square) == (chess.BISHOP, chess.F6)
    (castle,) = plans.parse_hint("O-O-O")
    assert castle.castle == "O-O-O"
    assert plans.parse_hint("just words") == []


def test_hint_matches_by_piece_type_and_destination_ignoring_capture() -> None:
    board = chess.Board(REVIEW)
    nxd5 = board.parse_san("Nxd5")
    exd5 = board.parse_san("exd5")
    assert plans.hint_matches_move("...Nd5", board, nxd5)
    assert plans.hint_matches_move("Nxd5", board, nxd5)
    assert not plans.hint_matches_move("...Nd5", board, exd5)
    assert plans.hint_matches_move("...d5", board, exd5)


# ---------- matching ----------


def test_hedgehog_d5_break_is_pv_match_when_the_line_contains_d5() -> None:
    board = chess.Board(HEDGEHOG)
    pv = _line(board, "Qb8 Qe1 Rfd8 Bg5 Ne5 Be2 d5")
    found = plans.match_plans("hedgehog", "black", [pv], board, [])
    assert _by_title(found, "...d5 브레이크").status == "pv_match"
    assert _by_title(found, "...Ne5로 f3 비숍 교환 유도").status == "pv_match"
    # ...b5 is legal now but not in the line and not played yet
    assert _by_title(found, "...b5 브레이크").status == "later"
    assert all(p.side == "black" for p in found)


def test_white_plans_in_hedgehog_without_lines_are_later_or_unavailable() -> None:
    board = chess.Board(HEDGEHOG)
    found = plans.match_plans("hedgehog", "white", [], board, [])
    statuses = {p.title: p.status for p in found}
    assert statuses["Nd5 점프 또는 희생"] == "later"  # Nc3-d5 is legal for White
    assert statuses["킹사이드 공간 확장 g4-g5"] == "later"  # g4 legal


def test_executed_when_the_hint_move_already_happened() -> None:
    start = chess.Board(BEFORE_ND5)
    nd5 = start.parse_san("Nd5")
    board = start.copy()
    board.push(nd5)
    assert board.fen() == REVIEW
    found = plans.match_plans("hedgehog", "white", [], board, [nd5], start_board=start)
    assert _by_title(found, "Nd5 점프 또는 희생").status == "executed"


def test_unavailable_when_no_hint_move_is_legal_and_none_in_lines() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    found = plans.match_plans("open_center", "black", [], board, [])
    statuses = {p.title: p.status for p in found}
    assert statuses["열린 파일 점령 ...Rd8, ...Rc8"] == "unavailable"
    assert statuses["킹 중앙화"] == "later"  # ...Ke7 / ...Kf7 are legal


def test_unknown_structure_yields_no_plans() -> None:
    assert plans.match_plans("nope", "white", [], chess.Board(), []) == []


# ---------- mirroring ----------


def test_black_iqp_is_mirrored_and_plans_flip_sides_and_squares() -> None:
    board = chess.Board()
    for san in "d4 d5 c4 e6 Nc3 c5 cxd5 exd5 Nf3 Nc6 g3 Nf6 Bg2 Be7 O-O O-O Bg5 cxd4 Nxd4".split():
        board.push_san(san)
    assert plans.mirrored("iqp", board)
    black = plans.match_plans("iqp", "black", [], board, [], mirror=True)
    push = _by_title(black, "d5-d4 브레이크")
    assert push.moves_hint == ["...d4"]
    assert push.side == "black"
    white = plans.match_plans("iqp", "white", [], board, [], mirror=True)
    blockade = _by_title(white, "d4 봉쇄")
    assert blockade.moves_hint == ["Nd4"]
    assert "..." not in blockade.condition


def test_white_iqp_is_not_mirrored() -> None:
    board = chess.Board()
    for san in "e4 c6 d4 d5 exd5 cxd5 c4 Nf6 Nc3 e6 Nf3 Be7 cxd5 Nxd5".split():
        board.push_san(san)
    assert not plans.mirrored("iqp", board)
    assert not plans.mirrored("hanging_pawns", board)


def test_break_hints_only_keep_pawn_first_hints() -> None:
    board = chess.Board(HEDGEHOG)
    found = plans.match_plans("hedgehog", "black", [], board, [])
    assert plans.break_hints(_by_title(found, "...d5 브레이크")) == ["...d5"]
    assert plans.break_hints(_by_title(found, "...Ne5로 f3 비숍 교환 유도")) == []
