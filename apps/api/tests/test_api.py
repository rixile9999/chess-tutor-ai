from fastapi.testclient import TestClient

from chess_tutor.api import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_motifs_endpoint() -> None:
    res = client.post(
        "/motifs",
        json={"fen": "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21", "san": "Nxf6+"},
    )
    assert res.status_code == 200
    kinds = {m["kind"] for m in res.json()["motifs"]}
    assert kinds == {"discovered_attack", "fork"}


def test_motifs_rejects_illegal_san() -> None:
    res = client.post("/motifs", json={"fen": "8/8/8/8/8/8/8/K6k w - - 0 1", "san": "Qh5"})
    assert res.status_code == 422
