"""End to end through the HTTP API with the real engine: import a PGN, analyse it, review the
worst move, read the profile and opening map, cut puzzles and schedule them, ask for a
sparring move. One 30-move rapid game at depth 8 keeps the whole flow under a minute.

Maia-2 is routed to the Stockfish fallback and the LLM is disabled, so the test runs with a
Stockfish binary alone; the language and human views still go through the same code paths.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient

from chess_tutor.config import get_settings
from chess_tutor.engine import find_stockfish
from chess_tutor.jobs import runner
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import maia as maia_svc

pytestmark = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")

USERNAME = "tutee"
DEPTH = 8

# A plausible 1500-level rapid game (Italian, both sides castle short). White (the user) gives
# up a knight with 22.Nxh6?? and a pawn with 30.d4?, then resigns: 0-1.
MOVES = (
    "e4 e5 Nf3 Nc6 Bc4 Nf6 d3 Bc5 O-O d6 Nc3 O-O Bg5 h6 Bh4 Bg4 h3 Bxf3 Qxf3 Nd4 "
    "Qd1 c6 Kh1 b5 Bb3 a5 a3 Qb6 Ne2 Nxb3 cxb3 g5 Bg3 Nh5 Qd2 Nxg3+ Nxg3 Kg7 Qe2 Rae8 "
    "Nf5+ Kh7 Nxh6 Kxh6 Qd2 Kg7 f4 gxf4 Qxf4 Qd8 Qf5 Rh8 Rf3 Rh6 Raf1 Qe7 Qg4+ Kh7 d4 Bxd4"
).split()
KNIGHT_BLUNDER_PLY = 43
"""22.Nxh6?? — the knight is simply taken."""


def build_pgn() -> str:
    """The game above as PGN, replayed with python-chess so every move is legal, with the
    headers a chess.com export carries and today's date so it falls inside the profile
    window."""
    game = chess.pgn.Game()
    today = datetime.now(UTC)
    game.headers["Event"] = "Live Chess"
    game.headers["Site"] = "Chess.com"
    game.headers["Date"] = today.strftime("%Y.%m.%d")
    game.headers["UTCDate"] = today.strftime("%Y.%m.%d")
    game.headers["UTCTime"] = today.strftime("%H:%M:%S")
    game.headers["White"] = USERNAME
    game.headers["Black"] = "rival_1500"
    game.headers["WhiteElo"] = "1512"
    game.headers["BlackElo"] = "1498"
    game.headers["TimeControl"] = "600+0"
    game.headers["Result"] = "0-1"
    node: chess.pgn.GameNode = game
    clock = 600.0
    for i, san in enumerate(MOVES):
        node = node.add_variation(node.board().parse_san(san))
        # 12 s per move for both sides; the knight blunder is played with 25 s left
        clock = 25.0 if i + 1 == KNIGHT_BLUNDER_PLY else 600.0 - 12.0 * ((i // 2) + 1)
        node.set_clock(clock)
    return str(game)


@pytest.fixture(autouse=True)
def _engine_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No Maia-2 weights, no LLM: Stockfish for the human view, templates for the prose."""
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    backend = maia_svc.EngineBackend()
    maia_svc.use_backends(backend, maia_svc.RandomBackend())
    yield
    maia_svc.use_backends()
    backend.close()


def _legal_sans(fen: str) -> set[str]:
    board = chess.Board(fen)
    return {board.san(mv) for mv in board.legal_moves}


def test_e2e_import_analyse_review_profile_train(client: TestClient) -> None:
    pgn = build_pgn()

    # ----- import -----
    res = client.post("/games/import/pgn", json={"pgn": pgn, "username": USERNAME})
    assert res.status_code == 200, res.text
    imported = res.json()
    assert imported["imported"] == 1 and imported["skipped"] == 0 and imported["errors"] == []
    assert imported["user_id"] is not None
    game_id = imported["game_ids"][0]

    res = client.get(f"/games/{game_id}")
    assert res.status_code == 200, res.text
    game = res.json()
    assert game["white"] == USERNAME and game["user_color"] == "white"
    assert game["ply_count"] == len(MOVES) and len(game["moves"]) == len(MOVES)
    assert game["moves"][KNIGHT_BLUNDER_PLY - 1]["san"] == "Nxh6"
    assert game["moves"][KNIGHT_BLUNDER_PLY - 1]["clock"] == 25.0
    assert game["eco"] and game["opening_name"]
    assert game["analysis_status"] == "none"

    listed = client.get("/games", params={"user": USERNAME})
    assert listed.status_code == 200 and [g["id"] for g in listed.json()] == [game_id]

    # ----- analysis -----
    res = client.post(f"/analysis/{game_id}", params={"depth": DEPTH, "multipv": 2})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending" and res.json()["depth"] == DEPTH

    job = client.portal.call(runner.wait, analysis_svc.job_key(game_id), 180.0)
    assert job.status == "done", job.error

    res = client.get(f"/analysis/{game_id}")
    assert res.status_code == 200, res.text
    analysis = res.json()
    assert analysis["status"] == "done" and analysis["error"] is None
    assert analysis["depth"] == DEPTH and analysis["engine"].startswith("stockfish")
    moves = analysis["moves"]
    assert len(moves) == len(MOVES)
    assert len(analysis["summary"]["eval_series"]) == len(MOVES) + 1
    assert 0 < analysis["summary"]["accuracy_white"] <= 100
    assert 0 < analysis["summary"]["accuracy_black"] <= 100
    white_counts = analysis["summary"]["counts"]["white"]
    assert white_counts["blunder"] >= 1, white_counts
    knight = moves[KNIGHT_BLUNDER_PLY - 1]
    assert knight["san"] == "Nxh6" and knight["color"] == "white"
    assert knight["classification"] in ("mistake", "blunder"), knight
    assert knight["clock"] == 25.0

    # the user's worst move of the game
    worst = max((m for m in moves if m["color"] == "white"), key=lambda m: m["win_prob_loss"])
    assert worst["classification"] == "blunder", worst
    assert worst["win_prob_loss"] >= knight["win_prob_loss"] > 0.15

    # ----- review -----
    res = client.get(f"/review/{game_id}")
    assert res.status_code == 200, res.text
    move_list = res.json()
    assert len(move_list) == len(MOVES)
    assert move_list[worst["ply"] - 1]["classification"] == worst["classification"]

    res = client.get(f"/review/{game_id}/{worst['ply']}", params={"rating": 1500})
    assert res.status_code == 200, res.text
    review = res.json()
    assert review["game_id"] == game_id and review["ply"] == worst["ply"]
    assert review["san"] == worst["san"] and review["classification"] == worst["classification"]
    assert review["fen_before"] == worst["fen_before"]
    assert review["refutation"] is not None
    assert review["refutation"]["main_line"], review["refutation"]
    assert review["refutation"]["main_line"][0] in _legal_sans(worst["fen_after"])
    assert review["alternatives"] and any(a["is_best"] for a in review["alternatives"])
    comparison = review["comparison"]
    assert comparison is not None
    assert {comparison["a_san"], comparison["b_san"]} == {worst["san"], worst["best_move_san"]}
    human = review["human"]
    assert human is not None and human["rating"] == 1500 and human["source"] == "engine"
    assert human["move_probs"] and human["played_prob"] is not None
    assert human["natural_reason"]
    explanation = review["explanation"]
    assert explanation["headline"] and explanation["lead"]
    assert explanation["source"] == "template"
    assert explanation["total_claims"] >= explanation["verified_claims"] >= 0
    assert explanation["verified"] is True, explanation
    assert review["strategy"] is not None and review["strategy"]["structure"] is not None
    assert review["arrows"]
    assert set(review["highlights"]) == {worst["uci"][:2], worst["uci"][2:4]}

    # the second request is served from the cache and is identical
    again = client.get(f"/review/{game_id}/{worst['ply']}", params={"rating": 1500})
    assert again.status_code == 200 and again.json() == review

    # the knight blunder: the refutation is simply taking the knight
    res = client.get(f"/review/{game_id}/{KNIGHT_BLUNDER_PLY}", params={"rating": 1500})
    assert res.status_code == 200, res.text
    knight_review = res.json()
    assert knight_review["san"] == "Nxh6"
    assert knight_review["refutation"]["main_line"][0] == "Kxh6", knight_review["refutation"]
    assert any(a["orig"] == "h7" and a["dest"] == "h6" for a in knight_review["arrows"])
    assert knight_review["explanation"]["verified"] is True
    assert knight_review["explanation"]["headline"] and knight_review["explanation"]["lead"]

    # ----- profile -----
    res = client.get(f"/profile/{USERNAME}")
    assert res.status_code == 200, res.text
    profile = res.json()
    assert profile["username"] == USERNAME and profile["platform"] == "local"
    assert profile["games"] == 1 and profile["analyzed_games"] == 1
    assert profile["summary_text"]
    phases = profile["phase_accuracy"]
    assert phases is not None
    assert phases["opening_moves"] + phases["middlegame_moves"] + phases["endgame_moves"] == 30
    assert phases["baseline_band"] == "1400-1600"
    assert profile["time"] is not None
    assert profile["time"]["moves_under_30s"] == 1
    assert profile["time"]["blunder_rate_under_30s"] == 1.0
    assert profile["training"] is not None and profile["training"]["due_puzzles"] == 0
    assert client.get("/profile/nobody").status_code == 404

    # ----- openings -----
    res = client.get(
        "/openings/map", params={"username": USERNAME, "color": "white", "min_games": 1}
    )
    assert res.status_code == 200, res.text
    opening_map = res.json()
    assert opening_map["color"] == "white" and opening_map["total_games"] == 1
    ids = {n["id"] for n in opening_map["nodes"]}
    assert opening_map["root"] in ids
    assert len(opening_map["nodes"]) == 13 and len(opening_map["edges"]) == 12
    assert all(e["source"] in ids and e["target"] in ids for e in opening_map["edges"])
    assert [n["san"] for n in opening_map["nodes"] if n["depth"] == 1] == ["e4"]
    assert any(n["name"] for n in opening_map["nodes"])

    res = client.get(
        "/openings/map", params={"username": USERNAME, "color": "black", "min_games": 1}
    )
    assert res.status_code == 404

    res = client.get(
        "/openings/heatmap",
        params={"username": USERNAME, "color": "white", "piece": "wc1", "through_move": 15},
    )
    assert res.status_code == 200, res.text
    heatmap = res.json()
    assert heatmap["games"] == 1 and sum(heatmap["squares"].values()) == pytest.approx(1.0)
    assert heatmap["squares"] == {"h4": 1.0}  # 7.Bg5 8.Bh4, still there after move 15

    res = client.get("/openings/breaks", params={"username": USERNAME, "color": "white"})
    assert res.status_code == 200, res.text
    assert all(len(b["histogram"]) == b["to_move"] - b["from_move"] + 1 for b in res.json())

    # ----- training -----
    res = client.post(f"/training/puzzles/from-game/{game_id}")
    assert res.status_code == 200, res.text
    puzzles = res.json()
    assert puzzles, "the knight blunder should have produced a puzzle"
    for puzzle in puzzles:
        assert puzzle["source_game_id"] == game_id and puzzle["source_ply"] is not None
        board = chess.Board(puzzle["fen"])
        assert puzzle["orientation"] == ("white" if board.turn else "black")
        assert len(puzzle["solution"]) % 2 == 1
        for uci in puzzle["solution"]:
            board.push(chess.Move.from_uci(uci))
        assert puzzle["reps"] == 0 and puzzle["interval_days"] == 0.0
    knight_puzzle = next(p for p in puzzles if p["source_ply"] == KNIGHT_BLUNDER_PLY)
    assert knight_puzzle["orientation"] == "black"
    assert knight_puzzle["solution"][0] == "h7h6"

    # the same game again creates nothing new
    assert client.post(f"/training/puzzles/from-game/{game_id}").json() == []

    res = client.get("/training/puzzles/due", params={"username": USERNAME})
    assert res.status_code == 200, res.text
    due = res.json()
    assert {p["id"] for p in due} == {p["id"] for p in puzzles}

    res = client.get(f"/training/puzzles/{knight_puzzle['id']}")
    assert res.status_code == 200 and res.json()["fen"] == knight_puzzle["fen"]

    res = client.post(
        f"/training/puzzles/{knight_puzzle['id']}/attempt", json={"correct": True, "seconds": 9}
    )
    assert res.status_code == 200, res.text
    assert res.json()["reps"] == 1 and res.json()["interval_days"] == 1.0

    res = client.get("/training/puzzles/due", params={"username": USERNAME})
    assert knight_puzzle["id"] not in {p["id"] for p in res.json()}

    res = client.get("/training/summary", params={"username": USERNAME})
    assert res.status_code == 200 and res.json()["due_puzzles"] == len(puzzles) - 1

    res = client.get(f"/profile/{USERNAME}")
    assert res.json()["training"]["due_puzzles"] == len(puzzles) - 1

    # ----- sparring -----
    res = client.post("/maia/move", json={"fen": knight_puzzle["fen"], "rating": 1500})
    assert res.status_code == 200, res.text
    sparring = res.json()
    assert sparring["san"] in _legal_sans(knight_puzzle["fen"])
    assert chess.Board(knight_puzzle["fen"]).parse_san(sparring["san"]).uci() == sparring["uci"]
    assert sparring["source"] == "engine"
    assert sum(sparring["probs"].values()) == pytest.approx(1.0, abs=1e-3)
    assert sparring["probs"]["Kxh6"] == max(sparring["probs"].values())

    res = client.get("/maia/status")
    assert res.status_code == 200 and res.json()["backend"] == "engine"
