from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_motifs_endpoint(client: TestClient) -> None:
    res = client.post(
        "/positions/motifs",
        json={"fen": "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 1 21", "san": "Nxf6+"},
    )
    assert res.status_code == 200
    kinds = {m["kind"] for m in res.json()["motifs"]}
    assert kinds == {"discovered_attack", "fork"}


def test_motifs_rejects_illegal_san(client: TestClient) -> None:
    res = client.post(
        "/positions/motifs", json={"fen": "8/8/8/8/8/8/8/K6k w - - 0 1", "san": "Qh5"}
    )
    assert res.status_code == 422


def test_router_stubs_mounted(client: TestClient) -> None:
    for name in ("games", "analysis", "review", "profile", "openings", "training", "maia"):
        assert client.get(f"/{name}/_status").status_code == 200


def test_oversized_ids_are_rejected_not_500(client: TestClient) -> None:
    huge = "9" * 25
    assert client.get(f"/games/{huge}").status_code == 422
    assert client.get(f"/analysis/{huge}").status_code == 422
    assert client.get(f"/review/{huge}/1").status_code == 422
    assert client.get(f"/training/puzzles/{huge}").status_code == 422
