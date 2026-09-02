import chess

from chess_tutor.motifs import discovered_attacks, forks

# Review example from the mockup: after 20...Qd7?? White plays 21.Nxf6+ and Rd1 hits Qd7.
AFTER_QD7 = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21"


def test_discovered_attack_on_queen_with_check() -> None:
    board = chess.Board(AFTER_QD7)
    move = board.parse_san("Nxf6+")
    found = discovered_attacks(board, move)
    assert len(found) == 1
    motif = found[0]
    assert motif.attacker == chess.D1
    assert motif.targets == (chess.D7,)
    assert motif.with_check is True


def test_no_discovery_before_the_blunder() -> None:
    board = chess.Board("5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21")
    move = board.parse_san("Nxf6+")
    assert discovered_attacks(board, move) == []


def test_knight_fork_is_flagged_unsafe_when_capturable() -> None:
    board = chess.Board(AFTER_QD7)
    move = board.parse_san("Nxf6+")
    (fork,) = forks(board, move)
    assert set(fork.targets) == {chess.D7, chess.G8}
    assert fork.safe is False  # ...Bxf6 or ...gxf6 removes the knight


def test_safe_royal_fork() -> None:
    board = chess.Board("3k3r/8/8/4N3/8/8/8/4K3 w - - 0 1")
    move = board.parse_san("Nf7+")
    (fork,) = forks(board, move)
    assert set(fork.targets) == {chess.D8, chess.H8}
    assert fork.with_check is True
    assert fork.safe is True
