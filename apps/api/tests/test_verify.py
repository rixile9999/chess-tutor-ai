import pytest

from chess_tutor.verify import Claim, play_line, verify

FEN = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21"


def test_attack_claim_holds_after_the_knight_moves() -> None:
    after = play_line(FEN, ["Nxf6+"]).fen()
    assert verify(Claim(kind="attacks", fen=after, subject="d1", object="d7")).holds


def test_attack_claim_fails_while_the_knight_blocks() -> None:
    assert not verify(Claim(kind="attacks", fen=FEN, subject="d1", object="d7")).holds


def test_legal_move_claim() -> None:
    assert verify(Claim(kind="legal_move", fen=FEN, object="Nxe7+")).holds
    assert not verify(Claim(kind="legal_move", fen=FEN, object="Nxg7")).holds


def test_review_lines_are_legal() -> None:
    play_line(FEN, ["Nxf6+", "Bxf6", "Rxd7"])
    play_line(FEN, ["Nxf6+", "Kh8", "Nxd7"])
    play_line(FEN, ["Nxe7+", "Qxe7"])
    with pytest.raises(ValueError):
        play_line(FEN, ["Nxf6+", "Qxf6"])
