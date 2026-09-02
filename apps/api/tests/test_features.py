import chess

from chess_tutor.features import (
    LABEL_ACTIVITY,
    LABEL_BISHOPS,
    LABEL_FILES,
    LABEL_KING,
    LABEL_MATERIAL,
    LABEL_PASSED,
    LABEL_PAWNS,
    LABEL_SPACE,
    Features,
    feature_diff,
    feature_scores,
    static_features,
    summarize_features,
)

IQP = "r1bq1rk1/pp2bppp/2n1pn2/8/2BP4/2N2N2/PP3PPP/R2QR1K1 w - - 0 10"
LABELS = [
    LABEL_MATERIAL,
    LABEL_PAWNS,
    LABEL_ACTIVITY,
    LABEL_KING,
    LABEL_SPACE,
    LABEL_PASSED,
    LABEL_FILES,
    LABEL_BISHOPS,
]


def test_start_position_is_symmetric() -> None:
    f = static_features(chess.Board())
    assert f.fen == chess.STARTING_FEN
    for side in (f.white, f.black):
        assert side.material == 39
        assert side.pawn_count == 8
        assert side.isolated_pawns == side.doubled_pawns == side.passed_pawns == []
        assert side.backward_pawns == []
        assert side.pawn_islands == 1
        assert side.open_files == side.half_open_files == []
        assert side.king_pawn_shield == 3
        assert side.king_zone_attackers == 0
        assert side.bishop_pair is True
        assert side.space == 0
        assert side.outposts == []
    assert f.white.king_square == "e1" and f.black.king_square == "e8"
    assert f.white.mobility == f.black.mobility
    assert f.side("white") is f.white and f.side("black") is f.black


def test_isolated_queen_pawn_and_files() -> None:
    f = static_features(chess.Board(IQP))
    assert f.white.isolated_pawns == ["d4"]
    assert f.white.passed_pawns == []  # the e6 pawn still covers d5
    assert f.white.pawn_islands == 3  # a2-b2, d4, f2-g2-h2
    assert f.black.pawn_islands == 2
    assert f.white.open_files == ["c"] and f.black.open_files == ["c"]
    assert f.white.half_open_files == ["e"]
    assert f.black.half_open_files == ["d"]
    assert f.white.rooks_on_open_files == ["e1"]
    assert f.white.king_square == "g1" and f.white.king_pawn_shield == 3


def test_passed_doubled_and_backward_pawns() -> None:
    passed = static_features(chess.Board("8/8/8/3P4/8/8/8/K6k w - - 0 1"))
    assert passed.white.passed_pawns == ["d5"]
    assert passed.white.isolated_pawns == ["d5"]

    doubled = static_features(chess.Board("4k3/8/8/8/8/2P5/2P5/4K3 w - - 0 1"))
    assert doubled.white.doubled_pawns == ["c2", "c3"]

    # Philidor-like: the d3 pawn cannot reach d4 because ...e5 guards it, and e4 is ahead.
    backward = static_features(chess.Board("4k3/8/3p4/4p3/4P3/3P4/8/4K3 w - - 0 1"))
    assert backward.white.backward_pawns == ["d3"]
    assert backward.white.isolated_pawns == []
    assert backward.black.backward_pawns == ["d6"]


def test_doubled_row_counts_extra_pawns_like_the_score() -> None:
    # Three c-pawns are two pawns too many on one file: the row says 2, not "one file".
    f = static_features(chess.Board("4k3/8/8/8/2P5/2P5/2PP4/4K3 w - - 0 1"))
    row = next(r for r in summarize_features(f, "white") if r.feature == LABEL_PAWNS)
    assert f.white.doubled_pawns == ["c2", "c3", "c4"]
    assert row.a == "이중 c폰 2, 폰 섬 1"
    black = static_features(chess.Board("4k3/2pp4/2p5/2p5/8/8/8/4K3 w - - 0 1"))
    black_row = next(r for r in summarize_features(black, "black") if r.feature == LABEL_PAWNS)
    assert black_row.a == "이중 c폰 2, 폰 섬 1"


def test_outpost_requires_pawn_support_and_no_pawn_lever() -> None:
    # Sveshnikov-style d5 knight: e4 supports it and no black c/e pawn can ever chase it.
    board = chess.Board("r2qkb1r/5ppp/p2p4/1p1Np3/4P3/8/PPP2PPP/R2QKB1R w KQkq - 0 1")
    f = static_features(board)
    assert f.white.outposts == ["d5"]
    # With a black e6 pawn the knight can be captured at once, so d5 is not an outpost.
    board.set_piece_at(chess.E5, None)
    board.set_piece_at(chess.E6, chess.Piece(chess.PAWN, chess.BLACK))
    assert static_features(board).white.outposts == []


def test_king_zone_attackers_and_shield() -> None:
    f = static_features(chess.Board("6k1/5ppp/8/7Q/8/8/5PPP/6K1 b - - 0 1"))
    assert f.black.king_zone_attackers == 1  # Qh5 hits h7
    assert f.black.king_pawn_shield == 3
    assert f.white.king_zone_attackers == 0
    assert f.white.bishop_pair is False


def test_bishop_pair_needs_both_square_colours() -> None:
    same = static_features(chess.Board("4k3/8/8/8/8/4B3/8/2B1K3 w - - 0 1"))
    assert same.white.bishop_pair is False  # c1 and e3 are both dark
    pair = static_features(chess.Board("4k3/8/8/8/8/8/8/2B1KB2 w - - 0 1"))
    assert pair.white.bishop_pair is True  # c1 dark, f1 light


def test_space_and_center_control() -> None:
    f = static_features(chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"))
    assert f.white.space == 5  # e4 hits d5/f5, Qd1 reaches h5, Bf1 reaches b5/a6
    assert f.white.center_control > f.black.center_control


def test_summary_rows_use_korean_labels() -> None:
    f = static_features(chess.Board(IQP))
    rows = summarize_features(f, "black")
    assert [r.feature for r in rows] == LABELS
    pawns = next(r for r in rows if r.feature == LABEL_PAWNS)
    assert pawns.a == "약점 없음, 폰 섬 2"
    assert pawns.b == "고립 d폰 1, 폰 섬 3"
    assert pawns.delta is not None and pawns.delta > 0
    files = next(r for r in rows if r.feature == LABEL_FILES)
    assert files.a == "열린 c파일, 반열림 d파일, 룩/퀸 배치 d8"
    for row in rows:
        assert "—" not in row.a and "—" not in row.b


def test_feature_diff_sign_follows_pov() -> None:
    a = static_features(chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 0 1"))  # white up a rook
    b = static_features(chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1"))
    rows_white = feature_diff(a, b, "white")
    rows_black = feature_diff(a, b, "black")
    material_white = next(r for r in rows_white if r.feature == LABEL_MATERIAL)
    material_black = next(r for r in rows_black if r.feature == LABEL_MATERIAL)
    assert material_white.delta == 5.0
    assert material_black.delta == -5.0
    assert material_white.a == "5점 (상대 0점)"
    assert [r.feature for r in rows_white] == LABELS


def test_feature_diff_matches_mockup_comparison() -> None:
    # 20...Nxd5 21.exd5 exd5 versus 20...exd5 21.exd5: the second leaves White a passed d5 pawn.
    before = chess.Board("5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 1 20")
    a = before.copy()
    for san in ("Nxd5", "exd5", "exd5"):
        a.push_san(san)
    b = before.copy()
    for san in ("exd5", "exd5"):
        b.push_san(san)
    rows = feature_diff(static_features(a), static_features(b), "black")
    passed = next(r for r in rows if r.feature == LABEL_PASSED)
    assert passed.a == "통과폰 d5 (상대 통과폰 없음)"
    assert passed.b == "통과폰 없음 (상대 통과폰 d5)"
    assert passed.delta is not None and passed.delta > 0
    scores = feature_scores(static_features(b), "black")
    assert scores[LABEL_PASSED] < 0


def test_features_round_trip_through_pydantic() -> None:
    f = static_features(chess.Board(IQP))
    again = Features.model_validate(f.model_dump())
    assert again == f
