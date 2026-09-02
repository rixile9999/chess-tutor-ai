import chess

from chess_tutor.structure import STRUCTURE_NAMES, classify, key_at, timeline

HEDGEHOG = "2r2rk1/pbqnbppp/1p1ppn2/8/2PNP3/2N1BB2/PP1Q1PPP/2RR2K1 b - - 4 14"
OPEN_CENTER = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21"
IQP = "r1bq1rk1/pp2bppp/2n1pn2/8/2BP4/2N2N2/PP3PPP/R2QR1K1 w - - 0 10"
QGD_BEFORE_EXCHANGE = "r1bq1rk1/pp1nbppp/2p1pn2/3p4/2PP4/2N1PN2/PPQ1BPPP/R1B2RK1 w - - 0 9"


def play(sans: str, start: str | None = None) -> chess.Board:
    board = chess.Board(start) if start else chess.Board()
    for san in sans.split():
        board.push_san(san)
    return board


def test_hedgehog() -> None:
    info = classify(chess.Board(HEDGEHOG))
    assert info.key == "hedgehog"
    assert info.name == "헤지호그"
    assert info.side == "black"
    assert 0 < info.confidence <= 1
    assert set(info.defining_pawns) == {"a7", "b6", "d6", "e6", "c4", "e4"}


def test_open_center() -> None:
    info = classify(chess.Board(OPEN_CENTER))
    assert info.key == "open_center"
    assert info.name == "오픈 센터"
    assert info.side == "both"


def test_isolated_queen_pawn_white_and_mirrored() -> None:
    info = classify(chess.Board(IQP))
    assert info.key == "iqp"
    assert info.name == "고립 d폰"
    assert info.side == "white"
    assert info.defining_pawns == ["d4"]
    mirrored = classify(chess.Board(IQP).mirror())
    assert mirrored.key == "iqp"
    assert mirrored.side == "black"
    assert mirrored.defining_pawns == ["d5"]


def test_carlsbad_after_exchange() -> None:
    board = play("cxd5 exd5", QGD_BEFORE_EXCHANGE)
    info = classify(board)
    assert info.key == "carlsbad"
    assert info.name == "칼스바드"
    assert info.side == "white"
    assert {"d4", "d5", "c6"} <= set(info.defining_pawns)
    assert classify(chess.Board(QGD_BEFORE_EXCHANGE)).key != "carlsbad"


def test_exchange_caro_kann_is_a_mirrored_carlsbad() -> None:
    info = classify(play("e4 c6 d4 d5 exd5 cxd5 Bd3 Nc6 c3 Nf6"))
    assert info.key == "carlsbad"
    assert info.side == "black"


def test_french_advance_chain() -> None:
    info = classify(play("e4 e6 d4 d5 e5 c5 c3 Nc6 Nf3"))
    assert info.key == "french_chain"
    assert info.name == "프렌치 사슬"
    assert info.side == "both"
    assert {"d4", "e5", "d5", "e6"} <= set(info.defining_pawns)


def test_kings_indian_and_benoni() -> None:
    kid = classify(play("d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O Be2 e5 d5"))
    assert kid.key == "kid" and kid.name == "킹스 인디언"
    benoni = classify(play("d4 Nf6 c4 c5 d5 e6 Nc3 exd5 cxd5 d6 e4 g6"))
    assert benoni.key == "benoni" and benoni.side == "black"


def test_sicilian_family() -> None:
    maroczy = classify(play("e4 c5 Nf3 g6 d4 cxd4 Nxd4 Nc6 c4"))
    assert maroczy.key == "maroczy" and maroczy.side == "white"
    scheveningen = classify(play("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 e6 Be2 a6"))
    assert scheveningen.key == "scheveningen" and scheveningen.name == "셰베닝겐"
    boleslavsky = classify(play("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be2 e5"))
    assert boleslavsky.key == "boleslavsky_hole" and boleslavsky.name == "볼레슬랍스키 홀"


def test_slav_caro_stonewall_symmetric_and_hanging() -> None:
    caro = classify(
        play("e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5 Ng3 Bg6 h4 h6 Nf3 Nd7 h5 Bh7 Bd3 Bxd3 Qxd3 e6")
    )
    assert caro.key == "slav_caro" and caro.confidence >= 0.9
    triangle = classify(play("d4 d5 c4 c6 Nf3 e6"))
    assert triangle.key == "slav_caro" and triangle.confidence < caro.confidence
    stonewall = classify(play("d4 f5 g3 Nf6 Bg2 e6 Nf3 d5 O-O c6 c4 Bd6"))
    assert stonewall.key == "stonewall" and stonewall.side == "black"
    assert {"d5", "e6", "f5"} <= set(stonewall.defining_pawns)
    symmetric = classify(play("e4 e6 d4 d5 exd5 exd5 Nf3 Nf6"))
    assert symmetric.key == "symmetrical_d" and symmetric.name == "대칭 d폰"
    hanging = classify(chess.Board("r2q1rk1/p3bppp/5n2/2pp4/8/1PN1PN2/P3BPPP/R2Q1RK1 w - - 0 1"))
    assert hanging.key == "hanging_pawns" and hanging.side == "black"
    assert set(hanging.defining_pawns) == {"c5", "d5"}


def test_closed_center_and_unclassified() -> None:
    locked = classify(chess.Board("rnbqkbnr/ppp2ppp/8/3pp3/3PP3/8/PPP2PPP/RNBQKBNR w KQkq - 0 3"))
    assert locked.key == "closed_center"
    assert set(locked.defining_pawns) == {"d4", "d5", "e4", "e5"}
    start = classify(chess.Board())
    assert start.key == "unclassified" and start.name == "미분류" and start.side is None
    assert key_at(chess.Board()) == "unclassified"


def test_endings_never_get_a_named_middlegame_structure() -> None:
    # A named structure carries plans about knights, rooks and bishops, so it may not be
    # claimed once the pieces (or the pawns) are gone.
    bare = [
        "4k3/p7/8/8/3P4/8/8/4K3 w - - 0 1",  # would match iqp on pawn placement alone
        "4k3/8/8/8/2PP4/8/5p2/4K3 w - - 0 1",  # would match hanging_pawns
        "8/5ppp/4k3/8/3P4/4K3/5PPP/8 w - - 0 1",  # realistic king-and-pawn ending
        "8/6k1/8/3K4/3P4/8/8/8 w - - 0 1",
    ]
    for fen in bare:
        info = classify(chess.Board(fen))
        assert info.key in {"open_center", "closed_center", "unclassified"}, (fen, info.key)
    # a rook ending keeps the generic centre description it always had
    assert classify(chess.Board("8/5pk1/6p1/8/8/4R3/r4PPP/6K1 w - - 0 40")).key == "open_center"
    # the same pawn placement with pieces on the board is still an IQP
    assert classify(chess.Board(IQP)).key == "iqp"
    thin = chess.Board("4k3/p7/8/8/3P4/8/8/4K3 w - - 0 1")
    thin.set_piece_at(chess.G1, chess.Piece(chess.KNIGHT, chess.WHITE))
    assert classify(thin).key != "iqp"  # one piece, still not enough pawns for a structure


def test_every_key_has_a_korean_name() -> None:
    for key, name in STRUCTURE_NAMES.items():
        assert name and "—" not in name, key


def test_timeline_merges_and_folds_short_spans() -> None:
    start = chess.Board()
    hedgehog = chess.Board(HEDGEHOG)
    open_center = chess.Board(OPEN_CENTER)
    boards = [start] * 3 + [hedgehog] * 10 + [open_center] * 2 + [hedgehog] * 3 + [open_center] * 6
    spans = timeline(boards)
    assert [(s.key, s.from_ply, s.to_ply) for s in spans] == [
        ("hedgehog", 0, 17),
        ("open_center", 18, 23),
    ]
    assert spans[0].name == "헤지호그"
    assert timeline([]) == []
    (only,) = timeline([start, start])
    assert (only.key, only.from_ply, only.to_ply) == ("unclassified", 0, 1)


def test_timeline_on_a_real_hedgehog_game() -> None:
    board = chess.Board()
    boards = [board.copy()]
    moves = (
        "e4 c5 Nf3 e6 d4 cxd4 Nxd4 a6 c4 Nf6 Nc3 Qc7 Be2 b6 O-O Bb7 Be3 d6 f3 Be7 "
        "Qd2 O-O Rfd1 Nbd7 Rac1 Rac8"
    )
    for san in moves.split():
        board.push_san(san)
        boards.append(board.copy())
    spans = timeline(boards)
    assert spans[-1].key == "hedgehog"
    assert spans[-1].to_ply == len(boards) - 1
    assert spans[0].from_ply == 0
    for first, second in zip(spans, spans[1:], strict=False):
        assert second.from_ply == first.to_ply + 1
        assert first.key != second.key
