import chess
import pytest

from chess_tutor.engine import Engine, find_stockfish

pytestmark = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")


def test_multipv_analysis() -> None:
    board = chess.Board("5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21")
    with Engine() as engine:
        lines = engine.analyse(board, depth=12, multipv=2)
    assert lines[0].pv[0] == board.parse_san("Nxf6+")
