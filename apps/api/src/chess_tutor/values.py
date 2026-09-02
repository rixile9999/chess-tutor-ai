"""Material values used by the concept-extraction layer."""

import chess

PIECE_VALUE: dict[chess.PieceType, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}


def value_at(board: chess.Board, square: chess.Square) -> int:
    piece = board.piece_at(square)
    return PIECE_VALUE[piece.piece_type] if piece else 0
