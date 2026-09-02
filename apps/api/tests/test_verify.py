import pytest
from fastapi.testclient import TestClient

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


def test_verify_endpoint_reports_one_verdict_per_claim(client: TestClient) -> None:
    """POST /positions/verify is the layer-4 guard's HTTP face: claims in, verdicts out."""
    after = play_line(FEN, ["Nxf6+"]).fen()
    res = client.post(
        "/positions/verify",
        json=[
            {"kind": "attacks", "fen": after, "subject": "d1", "object": "d7"},
            {"kind": "attacks", "fen": FEN, "subject": "d1", "object": "d7"},
            {"kind": "legal_move", "fen": FEN, "object": "Nxg7"},
        ],
    )
    assert res.status_code == 200, res.text
    verdicts = res.json()
    assert [v["holds"] for v in verdicts] == [True, False, False]
    # The claim comes back with the verdict, so a caller can log which sentence failed.
    assert verdicts[0]["claim"]["subject"] == "d1" and verdicts[0]["claim"]["object"] == "d7"
    assert verdicts[2]["detail"]  # an illegal SAN explains itself


def test_verify_endpoint_rejects_a_claim_with_no_kind(client: TestClient) -> None:
    res = client.post("/positions/verify", json=[{"fen": FEN, "subject": "d1"}])
    assert res.status_code == 422
