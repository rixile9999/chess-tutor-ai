"""Practice games (M7): POST /play/games, the plan report, and the practice filter on the
game list, the profile and the opening map.

Everything but the last test runs without an engine; the report test analyses a short game at
depth 8 and is skipped when no Stockfish binary is around.
"""

from __future__ import annotations

import io
from typing import Any

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db
from chess_tutor.config import get_settings
from chess_tutor.engine import find_stockfish
from chess_tutor.jobs import runner
from chess_tutor.models import Game, User
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import games as games_svc

USERNAME = "tutee"
CARLSBAD = "qgd-exchange-carlsbad"

# 1.d4 d5 2.c4 e6 3.Nc3 Nf6 4.e3 Be7 5.cxd5 exd5 6.Bd3 O-O 7.Nf3 c6 is the catalogue tabiya;
# White then plays the minority attack (b4-b5) and Black answers with ...c5.
CARLSBAD_GAME = (
    "d4 d5 c4 e6 Nc3 Nf6 e3 Be7 cxd5 exd5 Bd3 O-O Nf3 c6 O-O Nbd7 Qc2 Re8 "
    "b4 Nf8 b5 Bd6 bxc6 bxc6 Rb1 Ne6"
).split()
SCHOLARS_MATE = "e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7#".split()


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": USERNAME,
        "user_color": "white",
        "moves_san": CARLSBAD_GAME,
        "result": "*",
        "opponent": {"kind": "maia", "rating": 1500},
        "coach": {"preset": "learning", "hints": 2, "takebacks": 1, "alerts": 3},
        "opening_id": CARLSBAD,
        "practice_mode": "tabiya",
        "analyse": False,
    }
    body.update(overrides)
    return body


def headers_of(pgn: str) -> dict[str, str]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None
    return dict(game.headers)


# ---------- saving ----------


def test_save_stores_a_practice_game(client: TestClient) -> None:
    res = client.post("/play/games", json=payload())
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["analysis_status"] == "none"
    headers = headers_of(body["pgn"])
    assert headers["Event"] == "chess-tutor practice" and headers["Site"] == "chess-tutor"
    assert headers["White"] == USERNAME and headers["Black"] == "Maia 1500"
    assert headers["BlackElo"] == "1500" and headers["Result"] == "*"
    assert headers["Mode"] == "ai-black" and headers["Opponent"] == "maia:1500"
    assert headers["CoachPreset"] == "learning"
    assert (headers["Hints"], headers["Takebacks"], headers["Alerts"]) == ("2", "1", "3")
    assert headers["PracticeMode"] == "tabiya" and headers["OpeningId"] == CARLSBAD
    assert headers["Termination"] == "unfinished" and len(headers["PracticeNonce"]) == 32
    assert "FEN" not in headers and "SetUp" not in headers

    # The PGN we hand back is the one that was stored, and it replays.
    stored = client.get(f"/games/{body['game_id']}").json()
    assert stored["source"] == "practice" and stored["user_color"] == "white"
    assert stored["white"] == USERNAME and stored["black"] == "Maia 1500"
    assert [m["san"] for m in stored["moves"]] == CARLSBAD_GAME
    assert stored["eco"] and stored["opening_name"]
    assert stored["played_at"] is not None


async def test_saved_game_belongs_to_the_local_account(aclient: AsyncClient) -> None:
    res = await aclient.post("/play/games", json=payload())
    assert res.status_code == 201, res.text
    async with db.session_factory()() as session:
        user = (await session.execute(select(User))).scalars().one()
        assert (user.username, user.platform) == (USERNAME, "local")
        game = (await session.execute(select(Game))).scalars().one()
        assert game.user_id == user.id and game.source == "practice"
        assert game.headers["OpeningId"] == CARLSBAD


def test_saving_the_same_game_twice_stores_two_rows(client: TestClient) -> None:
    first = client.post("/play/games", json=payload()).json()
    second = client.post("/play/games", json=payload()).json()
    assert first["game_id"] != second["game_id"]
    # The nonce (and the timestamp) is what keeps the PGN hash from colliding.
    assert headers_of(first["pgn"])["PracticeNonce"] != headers_of(second["pgn"])["PracticeNonce"]
    listed = client.get("/games", params={"source": "practice"}).json()
    assert sorted(g["id"] for g in listed) == sorted([first["game_id"], second["game_id"]])


def test_illegal_san_is_rejected_with_the_ply(client: TestClient) -> None:
    res = client.post("/play/games", json=payload(moves_san=["d4", "d5", "Qxh8"]))
    assert res.status_code == 422
    assert "3번째 수" in res.json()["detail"]
    assert client.get("/games").json() == []


def test_empty_game_is_rejected(client: TestClient) -> None:
    assert client.post("/play/games", json=payload(moves_san=[])).status_code == 422


def test_manual_game_has_no_opponent(client: TestClient) -> None:
    body = client.post(
        "/play/games", json=payload(user_color=None, opponent=None, opening_id=None)
    ).json()
    headers = headers_of(body["pgn"])
    assert headers["White"] == USERNAME and headers["Black"] == "수동"
    assert headers["Mode"] == "manual" and headers["Opponent"] == "manual"
    assert "BlackElo" not in headers and "WhiteElo" not in headers
    stored = client.get(f"/games/{body['game_id']}").json()
    assert stored["user_color"] == "white" and stored["source"] == "practice"


def test_black_practice_game_puts_the_user_second(client: TestClient) -> None:
    body = client.post(
        "/play/games",
        json=payload(user_color="black", opponent={"kind": "stockfish", "rating": 1800}),
    ).json()
    headers = headers_of(body["pgn"])
    assert headers["White"] == "Stockfish 1800" and headers["Black"] == USERNAME
    assert headers["WhiteElo"] == "1800" and headers["Mode"] == "ai-white"
    assert client.get(f"/games/{body['game_id']}").json()["user_color"] == "black"


def test_start_fen_other_than_the_start_keeps_the_position(client: TestClient) -> None:
    board = chess.Board()
    for san in CARLSBAD_GAME[:14]:
        board.push_san(san)
    tabiya = board.fen()
    body = client.post(
        "/play/games", json=payload(start_fen=tabiya, moves_san=["O-O", "Nbd7", "b4"])
    ).json()
    headers = headers_of(body["pgn"])
    assert headers["FEN"] == tabiya and headers["SetUp"] == "1"
    stored = client.get(f"/games/{body['game_id']}").json()
    assert stored["initial_fen"] == tabiya
    assert [m["san"] for m in stored["moves"]] == ["O-O", "Nbd7", "b4"]


def test_a_game_from_a_tabiya_is_still_named(client: TestClient) -> None:
    """The start position of a tabiya game is itself a named book position, so the ECO and
    Opening headers (and with them the game row's opening) must come from it - a game that
    never leaves the tabiya has no move that would name it."""
    board = chess.Board()
    for san in CARLSBAD_GAME[:14]:
        board.push_san(san)
    body = client.post(
        "/play/games", json=payload(start_fen=board.fen(), moves_san=["Ne5", "Nbd7", "f4"])
    ).json()
    headers = headers_of(body["pgn"])
    assert headers["ECO"] == "D35"
    assert "Exchange" in headers["Opening"]
    stored = client.get(f"/games/{body['game_id']}").json()
    assert stored["eco"] == "D35" and "Exchange" in stored["opening_name"]


def test_bad_start_fen_is_rejected(client: TestClient) -> None:
    res = client.post("/play/games", json=payload(start_fen="not a fen", moves_san=["e4"]))
    assert res.status_code == 422 and "FEN" in res.json()["detail"]
    illegal = client.post(
        "/play/games", json=payload(start_fen="8/8/8/8/8/8/8/KK6 w - - 0 1", moves_san=["Kb2"])
    )
    assert illegal.status_code == 422


def test_clocks_end_up_in_the_pgn(client: TestClient) -> None:
    clocks = [600.0, 599.5, 585.0]
    body = client.post(
        "/play/games",
        json=payload(moves_san=CARLSBAD_GAME[:3], clocks=clocks, time_control="600+0"),
    ).json()
    assert "%clk" in body["pgn"]
    assert headers_of(body["pgn"])["TimeControl"] == "600+0"
    stored = client.get(f"/games/{body['game_id']}").json()
    assert [m["clock"] for m in stored["moves"]] == [600.0, 599.5, 585.0]
    assert stored["time_control"] == "600+0"


def test_checkmate_overrides_the_asked_result(client: TestClient) -> None:
    body = client.post(
        "/play/games", json=payload(moves_san=SCHOLARS_MATE, result="0-1", opening_id=None)
    ).json()
    headers = headers_of(body["pgn"])
    assert headers["Result"] == "1-0" and headers["Termination"] == "checkmate"
    assert client.get(f"/games/{body['game_id']}").json()["result"] == "1-0"


def test_requested_termination_is_kept(client: TestClient) -> None:
    body = client.post("/play/games", json=payload(result="0-1", termination="resign")).json()
    headers = headers_of(body["pgn"])
    assert headers["Result"] == "0-1" and headers["Termination"] == "resign"


async def test_user_rating_fills_the_elo_header(aclient: AsyncClient) -> None:
    """The account's rating is written as the user's Elo; an unknown name simply has none."""
    res = await aclient.post("/games/import/pgn", json={"pgn": _import_pgn(), "username": USERNAME})
    assert res.json()["imported"] == 1, res.text
    async with db.session_factory()() as session:
        user = (await session.execute(select(User))).scalars().one()
        await games_svc.set_user_ratings(session, user.id, 1440, None)

    body = (await aclient.post("/play/games", json=payload())).json()
    assert headers_of(body["pgn"])["WhiteElo"] == "1440"

    other = (await aclient.post("/play/games", json=payload(username="stranger"))).json()
    assert "WhiteElo" not in headers_of(other["pgn"])


# ---------- the practice filter ----------


def _import_pgn(result: str = "1-0") -> str:
    game = chess.pgn.Game()
    game.headers["Event"] = "Live Chess"
    game.headers["Date"] = "2026.09.01"
    game.headers["White"] = USERNAME
    game.headers["Black"] = "rival"
    game.headers["Result"] = result
    node: chess.pgn.GameNode = game
    for san in CARLSBAD_GAME[:12]:
        node = node.add_variation(node.board().parse_san(san))
    return str(game)


def _seed_one_of_each(client: TestClient) -> None:
    res = client.post("/games/import/pgn", json={"pgn": _import_pgn(), "username": USERNAME})
    assert res.json()["imported"] == 1, res.text
    assert client.post("/play/games", json=payload(result="1-0")).status_code == 201


def test_game_list_filters_by_source(client: TestClient) -> None:
    _seed_one_of_each(client)
    assert len(client.get("/games").json()) == 2
    practice = client.get("/games", params={"source": "practice"}).json()
    assert [g["source"] for g in practice] == ["practice"]
    imported = client.get("/games", params={"source": "pgn", "user": USERNAME}).json()
    assert [g["source"] for g in imported] == ["pgn"]
    assert client.get("/games", params={"source": "lichess"}).json() == []


def test_profile_excludes_practice_games_by_default(client: TestClient) -> None:
    _seed_one_of_each(client)
    default = client.get(f"/profile/{USERNAME}", params={"days": 3650}).json()
    assert default["games"] == 1
    included = client.get(
        f"/profile/{USERNAME}", params={"days": 3650, "include_practice": 1}
    ).json()
    assert included["games"] == 2


def test_opening_views_exclude_practice_games_by_default(client: TestClient) -> None:
    _seed_one_of_each(client)
    params = {"username": USERNAME, "color": "white"}
    assert client.get("/openings/map", params=params).json()["total_games"] == 1
    with_practice = client.get("/openings/map", params={**params, "include_practice": 1}).json()
    assert with_practice["total_games"] == 2

    heat = client.get("/openings/heatmap", params={**params, "piece": "wc1"}).json()
    assert heat["games"] == 1
    heat_all = client.get(
        "/openings/heatmap", params={**params, "piece": "wc1", "include_practice": 1}
    ).json()
    assert heat_all["games"] == 2

    breaks = client.get("/openings/breaks", params=params).json()
    b4 = next(b for b in breaks if b["label"] == "b2-b4")
    assert sum(b4["histogram"]) == 0, "the imported game stops before b4"
    breaks_all = client.get("/openings/breaks", params={**params, "include_practice": 1}).json()
    b4_all = next(b for b in breaks_all if b["label"] == "b2-b4")
    assert sum(b4_all["histogram"]) == 1


# ---------- plan report ----------


def test_plan_report_of_an_unknown_game_is_404(client: TestClient) -> None:
    assert client.get("/play/report/999").status_code == 404


@pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")
def test_analysis_is_queued_and_the_report_reads_the_plans(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Carlsbad practice game, analysed at depth 8, reports the minority attack as executed."""
    monkeypatch.setattr(get_settings(), "engine_depth", 8)
    monkeypatch.setattr(get_settings(), "engine_multipv", 2)

    res = client.post("/play/games", json=payload(result="1-0", analyse=True))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["analysis_status"] == "pending"
    game_id = body["game_id"]

    job = client.portal.call(runner.wait, analysis_svc.job_key(game_id), 300.0)
    assert job.status == "done", job.error
    assert client.get(f"/analysis/{game_id}").json()["status"] == "done"

    res = client.get(f"/play/report/{game_id}")
    assert res.status_code == 200, res.text
    report = res.json()
    assert report["game_id"] == game_id and report["side"] == "white"
    assert report["structure"]["key"] == "carlsbad"
    assert report["opening_id"] == CARLSBAD and report["practice_mode"] == "tabiya"
    assert report["opening_name"] == "QGD 익스체인지 (칼스바드)"

    titles = {p["title"] for p in report["executed"]}
    statuses = report["executed"] + report["pv_match"] + report["later"] + report["unavailable"]
    assert len(statuses) == 4, statuses
    assert all(p["side"] == "white" for p in statuses)
    minority = next(p for p in statuses if "소수 공격" in p["title"])
    assert minority["status"] == "executed", statuses
    assert "소수 공격" in " ".join(titles)
    assert any("b5" in b for b in report["breaks"]), report["breaks"]
    assert "칼스바드" in report["summary"] and "백" in report["summary"]
    assert "소수 공격" in report["summary"]
