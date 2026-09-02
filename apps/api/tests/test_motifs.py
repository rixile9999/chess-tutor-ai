import chess

from chess_tutor.motifs import (
    KOREAN_NAMES,
    Motif,
    back_rank,
    describe,
    detect,
    discovered_attacks,
    forks,
    hanging_pieces,
    mate_threats,
    overloads,
    pins,
    remove_defenders,
    see,
    skewers,
    trapped_pieces,
)

# Review example from the mockup: after 20...Qd7?? White plays 21.Nxf6+ and Rd1 hits Qd7.
AFTER_QD7 = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21"


def _detect(fen: str, san: str, detector=detect) -> list[Motif]:
    board = chess.Board(fen)
    return detector(board, board.parse_san(san))


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


# ---------- pins and skewers ----------


def test_relative_pin_knight_to_queen() -> None:
    # QGD: 1.d4 d5 2.c4 e6 3.Nc3 Nf6 4.Bg5 pins Nf6 to Qd8 through the empty e7 square.
    (pin,) = _detect(
        "rnbqkb1r/ppp2ppp/4pn2/3p4/2PP4/2N5/PP2PPPP/R1BQKBNR w KQkq - 2 4", "Bg5", pins
    )
    assert pin.attacker == chess.G5
    assert pin.targets == (chess.F6, chess.D8)
    assert pin.target_pieces == ("N", "Q")
    assert pin.with_check is False
    assert describe(pin) == "핀: Bg5가 Nf6을 Qd8에 묶음"


def test_absolute_pin_is_not_a_check() -> None:
    # 1.e4 c5 2.Nf3 d6 3.Nc3 Nc6 4.Bb5: d7 is empty so the knight is pinned to the king.
    (pin,) = _detect(
        "r1bqkbnr/pp2pppp/2np4/2p5/4P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 2 4", "Bb5", pins
    )
    assert pin.targets == (chess.C6, chess.E8)
    assert pin.with_check is False
    after = chess.Board("r1bqkbnr/pp2pppp/2np4/2p5/4P3/2N2N2/PPPP1PPP/R1BQKB1R w KQkq - 2 4")
    after.push_san("Bb5")
    assert after.is_pinned(chess.BLACK, chess.C6)
    assert describe(pin).startswith("절대 핀")


def test_existing_pin_is_not_reported_again() -> None:
    fen = "rnbqkb1r/ppp2ppp/4pn2/3p2B1/2PP4/2N5/PP2PPPP/R2QKBNR w KQkq - 4 5"
    assert _detect(fen, "h3", pins) == []


def test_no_pin_through_a_pawn() -> None:
    # Ruy Lopez Bb5: the d7 pawn stands between Nc6 and Ke8.
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
    assert _detect(fen, "Bb5", pins) == []


def test_skewer_king_in_front_of_queen() -> None:
    (skewer,) = _detect("8/4q3/8/4k3/8/8/8/R4K2 w - - 0 1", "Re1+", skewers)
    assert skewer.targets == (chess.E5, chess.E7)
    assert skewer.with_check is True
    assert skewer.safe is True
    assert describe(skewer) == "스큐어: Re1이 Ke5를 공격, 뒤에 Qe7"


# ---------- hanging, removal, overload ----------


def test_hanging_piece_created_by_the_move() -> None:
    (hang,) = _detect("4k3/8/2n5/8/8/8/8/3QK3 w - - 0 1", "Qd5", hanging_pieces)
    assert hang.attacker == chess.D5
    assert hang.targets == (chess.C6,)
    assert describe(hang) == "무방비 기물: Qd5가 Nc6을 공격"
    # the knight can run, so it is not trapped
    assert _detect("4k3/8/2n5/8/8/8/8/3QK3 w - - 0 1", "Qd5", trapped_pieces) == []


def test_quiet_developing_move_has_no_motifs() -> None:
    assert _detect(chess.STARTING_FEN, "Nf3") == []
    assert _detect("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3", "Bb5") == []


def test_remove_defender_by_capture() -> None:
    # Nc6 is the only defender of Ne5, which Nf3 attacks; Bxc6+ removes it.
    (motif,) = _detect("4k3/8/2n5/1B2n3/8/5N2/8/4K3 w - - 0 1", "Bxc6+", remove_defenders)
    assert motif.targets == (chess.C6, chess.E5)
    assert motif.target_pieces == ("N", "N")  # the captured defender keeps its letter
    assert describe(motif) == "수비수 제거: Ne5를 지키던 Nc6을 잡음"
    after = chess.Board("4k3/8/2n5/1B2n3/8/5N2/8/4K3 w - - 0 1")
    after.push_san("Bxc6+")
    assert not after.attackers(chess.BLACK, chess.E5)


def test_remove_defender_by_attack() -> None:
    # Black Rd8 alone guards Nd5, which Bb3 attacks; Rc1-c8+ hits the rook.
    fen = "3r2k1/6pp/8/3n4/8/1B6/6PP/2R3K1 w - - 0 1"
    found = [m for m in _detect(fen, "Rc8", remove_defenders)]
    assert found and found[0].targets == (chess.D8, chess.D5)
    assert describe(found[0]) == "수비수 제거: Rc8이 Nd5를 지키는 Rd8을 공격"


def test_overloaded_queen() -> None:
    # Qd7 alone guards Nc6 and Be6 (Re1 already hits e6); Bb5 attacks the knight.
    found = _detect("7k/3q4/2n1b3/8/8/8/8/4RBK1 w - - 0 1", "Bb5", overloads)
    (motif,) = found
    assert motif.targets == (chess.D7, chess.C6, chess.E6)
    assert motif.attacker == chess.B5
    assert describe(motif) == "과부하: Qd7이 Nc6과 Be6을 함께 지킴, Bb5가 Nc6을 공격"


# ---------- mate threats ----------


def test_back_rank_threat_after_capture() -> None:
    (motif,) = _detect("6k1/5ppp/8/n7/8/8/5PPP/R5K1 w - - 0 1", "Rxa5", back_rank)
    assert motif.targets == (chess.G8,)
    assert motif.line == ("Ra8#",)
    assert describe(motif) == "백랭크: Ra8# 위협"
    probe = chess.Board("6k1/5ppp/8/n7/8/8/5PPP/R5K1 w - - 0 1")
    probe.push_san("Rxa5")
    probe.push(chess.Move.null())
    probe.push_san("Ra8")
    assert probe.is_checkmate()


def test_back_rank_supersedes_generic_mate_threat_in_detect() -> None:
    kinds = [m.kind for m in _detect("6k1/5ppp/8/n7/8/8/5PPP/R5K1 w - - 0 1", "Rxa5")]
    assert "back_rank" in kinds
    assert "mate_threat" not in kinds


def test_scholars_mate_threat() -> None:
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    (motif,) = _detect(fen, "Qh5", mate_threats)
    assert motif.attacker == chess.H5
    assert motif.targets == (chess.E8,)
    assert motif.line == ("Qxf7#",)
    assert describe(motif) == "메이트 위협: Qxf7#"


def test_no_mate_threat_when_the_move_gives_check() -> None:
    assert _detect(AFTER_QD7, "Nxf6+", mate_threats) == []


# ---------- trapped pieces ----------


def test_knight_on_the_rim_is_trapped_by_pawn_push() -> None:
    fen = "6k1/6pp/8/4P2n/8/4P1P1/5P1P/6K1 w - - 0 1"
    (motif,) = _detect(fen, "g4", trapped_pieces)
    assert motif.targets == (chess.H5,)
    assert motif.target_pieces == ("N",)
    assert describe(motif) == "기물 트랩: g4 폰이 Nh5를 공격, 피할 칸 없음"
    after = chess.Board(fen)
    after.push_san("g4")
    for reply in after.legal_moves:  # every knight move lands on a pawn-guarded square
        if reply.from_square == chess.H5:
            assert after.is_attacked_by(chess.WHITE, reply.to_square)


def test_trapped_piece_wins_over_hanging_piece_in_detect() -> None:
    kinds = [m.kind for m in _detect("6k1/6pp/8/4P2n/8/4P1P1/5P1P/6K1 w - - 0 1", "g4")]
    assert kinds == ["trapped_piece"]


# ---------- rendering and integration ----------


def test_static_exchange_evaluation() -> None:
    board = chess.Board("4k3/8/2n5/8/8/8/8/3QK3 w - - 0 1")
    assert see(board, chess.C6, chess.WHITE) == 0  # queen cannot reach c6 yet
    board.push_san("Qd5")
    assert see(board, chess.C6, chess.WHITE) == 3
    defended = chess.Board("4k3/1p6/2n5/3Q4/8/8/8/4K3 b - - 0 1")
    assert see(defended, chess.C6, chess.WHITE) == 0  # QxN bxQ loses the queen


def test_detect_on_mockup_blunder_names_every_kind_in_korean() -> None:
    found = _detect(AFTER_QD7, "Nxf6+")
    kinds = {m.kind for m in found}
    # Nf6 was also Qd7's only defender, but the discovery already explains why the queen
    # falls, so that note is folded away.
    assert kinds == {"discovered_attack", "fork"}
    for motif in found:
        assert motif.kind in KOREAN_NAMES
        payload = motif.as_dict()
        assert payload["label"] == KOREAN_NAMES[motif.kind]
        assert payload["description"] == describe(motif)
        assert "—" not in str(payload["description"])
    fork = next(m for m in found if m.kind == "fork")
    assert describe(fork) == "나이트 포크: Qd7과 Kg8"
    disco = next(m for m in found if m.kind == "discovered_attack")
    assert describe(disco) == "디스커버드 어택: Nf6이 비켜서며 Rd1이 Qd7을 겨냥"


def test_motif_endpoint_returns_korean_label(client) -> None:
    response = client.post("/positions/motifs", json={"fen": AFTER_QD7, "san": "Nxf6+"})
    assert response.status_code == 200
    motifs = response.json()["motifs"]
    fork = next(m for m in motifs if m["kind"] == "fork")
    assert fork["label"] == "포크"
    assert fork["description"] == "나이트 포크: Qd7과 Kg8"
    assert fork["line"] == []
