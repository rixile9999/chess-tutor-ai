import chess

from chess_tutor.openings import classify_game, lookup


def test_lookup_by_transposition() -> None:
    a = chess.Board()
    for san in ["e4", "c5", "Nf3", "d6"]:
        a.push_san(san)
    b = chess.Board()
    for san in ["Nf3", "c5", "e4", "d6"]:
        b.push_san(san)
    assert lookup(a) is not None
    assert lookup(a) == lookup(b)
    assert "Sicilian" in lookup(a).name


def test_classify_game_reports_where_the_book_ends() -> None:
    board = chess.Board()
    moves = []
    for san in [
        "e4",
        "c5",
        "Nf3",
        "e6",
        "d4",
        "cxd4",
        "Nxd4",
        "Nf6",
        "Nc3",
        "d6",
        "a3",
        "h6",
        "h3",
        "a6",
    ]:
        mv = board.parse_san(san)
        moves.append(mv)
        board.push(mv)
    opening, left_at = classify_game(moves)
    assert opening is not None and opening.eco.startswith("B8")
    assert 10 <= left_at <= 14
