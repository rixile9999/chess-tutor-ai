"""Game import and listing: PGN parsing, dedupe, user colour, clocks, ordering, platform mocks."""

from __future__ import annotations

from pathlib import Path

import chess
import httpx
import respx
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db
from chess_tutor.models import Game, User
from chess_tutor.services import games as svc
from chess_tutor.services import importers

FIXTURES = Path(__file__).parent / "fixtures"
TWO_GAMES = (FIXTURES / "two_games.pgn").read_text(encoding="utf-8")
CHESSCOM_GAME = (FIXTURES / "chesscom_game.pgn").read_text(encoding="utf-8")
LICHESS_EXPORT = (FIXTURES / "lichess_export.pgn").read_text(encoding="utf-8")


# ---------- parsing ----------


def test_parse_two_games_headers_and_clocks() -> None:
    games = svc.parse_pgn_games(TWO_GAMES)
    assert len(games) == 2
    older, newer = games

    assert (older.white, older.black, older.result) == ("rival_two", "tutee", "0-1")
    assert (older.white_elo, older.black_elo) == (1571, 1550)
    assert older.time_control == "600+0"
    assert older.played_at is not None and older.played_at.isoformat() == "2026-08-22T18:03:41"
    # ECO/Opening headers are used as given.
    assert (older.eco, older.opening_name) == ("A00", "Barnes Opening: Fool's Mate")
    assert [m.clock for m in older.moves] == [600.0, 600.0, 598.0, 599.0]

    assert (newer.white, newer.black, newer.result) == ("tutee", "rival_one", "1-0")
    assert newer.played_at is not None and newer.played_at.isoformat() == "2026-08-29T09:12:00"
    # No ECO/Opening headers: the opening book fills them in from the moves.
    assert newer.eco == "C23" and newer.opening_name == "Bishop's Opening"
    assert newer.ply_count == 7
    assert [m.clock for m in newer.moves][:3] == [598.7, 597.2, 595.1]
    assert newer.moves[-1].san == "Qxf7#"

    # Each game keeps only its own raw PGN, and the sha1 ids differ.
    assert older.pgn.startswith('[Event "Rated Rapid game"]') and older.pgn.endswith("0-1")
    assert newer.pgn.startswith('[Event "Live Chess"]') and newer.pgn.endswith("1-0")
    assert older.source_id != newer.source_id
    assert all(len(g.source_id or "") == 40 for g in games)


def test_parsed_moves_replay_on_a_board() -> None:
    for game in svc.parse_pgn_games(TWO_GAMES):
        board = chess.Board(game.initial_fen)
        for i, info in enumerate(game.moves, start=1):
            move = board.parse_san(info.san)
            assert info.ply == i
            assert move.uci() == info.uci
            board.push(move)
            assert board.fen() == info.fen_after
        assert board.is_checkmate()
        assert board.result() == game.result


def test_source_id_ignores_whitespace_differences() -> None:
    games = svc.parse_pgn_games(TWO_GAMES)
    # CRLF endings and movetext wrapped onto a second line describe the same games.
    reflowed = TWO_GAMES.replace("\n", "\r\n").replace(" 2. ", "\r\n2. ")
    assert [g.source_id for g in svc.parse_pgn_games(reflowed)] == [g.source_id for g in games]


def test_parse_reports_broken_and_variant_games() -> None:
    broken = TWO_GAMES.replace("2. Bc4", "2. Bc5")
    report = svc.parse_pgn(broken)
    assert len(report.games) == 1 and report.games[0].black == "tutee"
    assert len(report.errors) == 1 and report.errors[0].startswith("2번째 게임")

    report = svc.parse_pgn(LICHESS_EXPORT)
    assert [g.headers["Site"] for g in report.games] == [
        "https://lichess.org/Zy98Xw76",
        "https://lichess.org/Qw34Er56",
    ]
    assert len(report.errors) == 1 and "Chess960" in report.errors[0]
    assert svc.parse_pgn("").games == [] and svc.parse_pgn("   \n").errors == []


def test_user_color_is_case_insensitive() -> None:
    older, newer = svc.parse_pgn_games(TWO_GAMES)
    assert older.color_of("TUTEE") == "black"
    assert newer.color_of("tutee") == "white"
    assert newer.color_of("nobody") is None and newer.color_of(None) is None


# ---------- storage ----------


async def test_upsert_dedupes_and_sets_user_color() -> None:
    parsed = svc.parse_pgn_games(TWO_GAMES)
    async with db.session_factory()() as session:
        first = await svc.upsert_games(session, parsed, "pgn", username="tutee")
        assert (first.imported, first.skipped, len(first.game_ids)) == (2, 0, 2)
        assert first.user_id is not None

        again = await svc.upsert_games(session, parsed, "pgn", username="tutee")
        assert (again.imported, again.skipped, again.game_ids) == (0, 2, [])
        assert again.user_id == first.user_id

        # The same game twice in one batch is stored once.
        twice = await svc.upsert_games(session, [parsed[0], parsed[0]], "chesscom")
        assert (twice.imported, twice.skipped) == (1, 1)

        user = await session.get(User, first.user_id)
        assert user is not None and (user.username, user.platform) == ("tutee", "local")
        rows = (await session.scalars(select(Game).order_by(Game.id))).all()
        assert [g.user_color for g in rows] == ["black", "white", None]
        assert all(g.user_id == first.user_id for g in rows[:2])
        assert rows[0].ply_count == 4 and rows[1].ply_count == 7


async def test_game_moves_and_boards_of() -> None:
    async with db.session_factory()() as session:
        result = await svc.upsert_games(session, svc.parse_pgn_games(TWO_GAMES), "pgn")
        game = await svc.get_game(session, result.game_ids[1])
    assert game is not None
    moves = svc.game_moves(game)
    boards = svc.boards_of(game)
    assert len(moves) == 7 and len(boards) == 8
    assert boards[0].fen() == chess.STARTING_FEN
    assert [b.fen() for b in boards[1:]] == [m.fen_after for m in moves]
    assert boards[-1].is_checkmate()
    assert moves[0].clock == 598.7 and moves[0].san == "e4" and moves[0].uci == "e2e4"
    assert svc.to_summary(game).analysis_status == "none"
    detail = svc.to_detail(game)
    assert detail.initial_fen == chess.STARTING_FEN and len(detail.moves) == 7


async def test_list_games_newest_first_and_user_filter() -> None:
    async with db.session_factory()() as session:
        await svc.upsert_games(session, svc.parse_pgn_games(TWO_GAMES), "pgn", username="tutee")
        await svc.upsert_games(session, svc.parse_pgn_games(CHESSCOM_GAME), "pgn")
        rows = await svc.list_games(session)
        assert [g.played_at.date().isoformat() for g in rows if g.played_at] == [
            "2026-08-30",
            "2026-08-29",
            "2026-08-22",
        ]
        mine = await svc.list_games(session, username="Tutee")
        assert [g.black for g in mine] == ["rival_one", "tutee"]
        page = await svc.list_games(session, limit=1, offset=1)
        assert len(page) == 1 and page[0].white == "tutee"


# ---------- endpoints ----------


def test_import_pgn_endpoint_then_list_and_detail(client: TestClient) -> None:
    res = client.post("/games/import/pgn", json={"pgn": TWO_GAMES, "username": "tutee"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["imported"], body["skipped"], body["errors"]) == (2, 0, [])
    assert len(body["game_ids"]) == 2 and body["user_id"] is not None

    res = client.post("/games/import/pgn", json={"pgn": TWO_GAMES, "username": "tutee"})
    assert res.json()["imported"] == 0 and res.json()["skipped"] == 2

    listing = client.get("/games", params={"user": "tutee"}).json()
    assert [g["played_at"][:10] for g in listing] == ["2026-08-29", "2026-08-22"]
    assert [g["user_color"] for g in listing] == ["white", "black"]
    assert listing[0]["analysis_status"] == "none" and listing[0]["source"] == "pgn"
    assert listing[1]["eco"] == "A00" and listing[0]["opening_name"] == "Bishop's Opening"

    detail = client.get(f"/games/{listing[1]['id']}").json()
    assert detail["initial_fen"] == chess.STARTING_FEN
    assert [m["san"] for m in detail["moves"]] == ["f3", "e5", "g4", "Qh4#"]
    assert [m["clock"] for m in detail["moves"]] == [600.0, 600.0, 598.0, 599.0]
    assert detail["moves"][3]["ply"] == 4 and detail["moves"][3]["uci"] == "d8h4"
    assert detail["pgn"].startswith('[Event "Rated Rapid game"]')


def test_import_pgn_reports_skipped_games(client: TestClient) -> None:
    broken = TWO_GAMES.replace("2. Bc4", "2. Bc5")
    body = client.post("/games/import/pgn", json={"pgn": broken}).json()
    assert (body["imported"], body["skipped"]) == (1, 1)
    assert len(body["errors"]) == 1 and "2번째 게임" in body["errors"][0]
    assert client.get("/games").json()[0]["user_color"] is None

    res = client.post("/games/import/pgn", json={"pgn": "\n"})
    assert res.status_code == 422 and "PGN" in res.json()["detail"]


def test_detail_404_and_delete(client: TestClient) -> None:
    assert client.get("/games/999").status_code == 404
    assert client.delete("/games/999").status_code == 404
    game_id = client.post("/games/import/pgn", json={"pgn": CHESSCOM_GAME}).json()["game_ids"][0]
    assert client.get(f"/games/{game_id}").status_code == 200
    assert client.delete(f"/games/{game_id}").status_code == 204
    assert client.get(f"/games/{game_id}").status_code == 404
    assert client.get("/games").json() == []


def test_status_stub_still_mounted(client: TestClient) -> None:
    assert client.get("/games/_status").json()["module"] == "games"


# ---------- platform imports (mocked) ----------


def _chesscom_archive() -> dict:
    return {
        "games": [
            {
                "url": "https://www.chess.com/game/live/123456789",
                "pgn": CHESSCOM_GAME,
                "time_control": "600",
                "end_time": 1756563005,
                "rated": True,
                "time_class": "rapid",
                "rules": "chess",
                "white": {"rating": 1602, "result": "resigned", "username": "rival_three"},
                "black": {"rating": 1555, "result": "win", "username": "Tutee"},
            },
            {
                "url": "https://www.chess.com/game/live/123456790",
                "pgn": CHESSCOM_GAME,
                "time_class": "blitz",
                "rules": "chess960",
                "white": {"rating": 1500, "username": "rival_three"},
                "black": {"rating": 1500, "username": "Tutee"},
            },
        ]
    }


async def test_chesscom_import(aclient: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        archives = mock.get("https://api.chess.com/pub/player/tutee/games/archives").mock(
            return_value=httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/tutee/games/2026/06",
                        "https://api.chess.com/pub/player/tutee/games/2026/07",
                        "https://api.chess.com/pub/player/tutee/games/2026/08",
                    ]
                },
            )
        )
        june = mock.get("https://api.chess.com/pub/player/tutee/games/2026/06").mock(
            return_value=httpx.Response(200, json={"games": []})
        )
        mock.get("https://api.chess.com/pub/player/tutee/games/2026/07").mock(
            return_value=httpx.Response(200, json={"games": []})
        )
        mock.get("https://api.chess.com/pub/player/tutee/games/2026/08").mock(
            return_value=httpx.Response(200, json=_chesscom_archive())
        )
        mock.get("https://api.chess.com/pub/player/tutee/stats").mock(
            return_value=httpx.Response(
                200,
                json={
                    "chess_rapid": {"last": {"rating": 1548}},
                    "chess_blitz": {"last": {"rating": 1402}},
                },
            )
        )

        res = await aclient.post("/games/import/chesscom", json={"username": "Tutee", "months": 2})
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["imported"], body["skipped"]) == (1, 0)
        assert archives.called and not june.called  # only the last two archives are fetched
        ua = archives.calls.last.request.headers["User-Agent"]
        assert ua.startswith("chess-tutor-ai")

        # Re-sync: the platform id keeps it from being imported twice.
        res = await aclient.post("/games/import/chesscom", json={"username": "tutee", "months": 1})
        assert res.json()["imported"] == 0 and res.json()["skipped"] == 1

    listing = (await aclient.get("/games", params={"user": "tutee"})).json()
    assert len(listing) == 1
    game = listing[0]
    assert (game["source"], game["source_id"]) == ("chesscom", "123456789")
    assert (game["white"], game["black"], game["user_color"]) == ("rival_three", "Tutee", "black")
    assert (game["white_elo"], game["black_elo"]) == (1602, 1555)
    assert game["played_at"].startswith("2026-08-30T14:20:05")
    assert game["eco"] == "D53" and game["ply_count"] == 10

    async with db.session_factory()() as session:
        user = await session.scalar(select(User).where(User.platform == "chesscom"))
        assert user is not None
        assert (user.username, user.rating_rapid, user.rating_blitz) == ("Tutee", 1548, 1402)
        stored = await session.scalar(select(Game))
        assert stored is not None and stored.headers["TimeClass"] == "rapid"


async def test_chesscom_unknown_user_is_502(aclient: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://api.chess.com/pub/player/ghost/games/archives").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )
        res = await aclient.post("/games/import/chesscom", json={"username": "ghost"})
    assert res.status_code == 502 and "ghost" in res.json()["detail"]


async def test_lichess_import(aclient: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        export = mock.get(url__regex=r"https://lichess\.org/api/games/user/tutee.*").mock(
            return_value=httpx.Response(200, text=LICHESS_EXPORT)
        )
        mock.get("https://lichess.org/api/user/tutee").mock(
            return_value=httpx.Response(
                200, json={"perfs": {"rapid": {"rating": 1541}, "blitz": {"rating": 1490}}}
            )
        )
        res = await aclient.post(
            "/games/import/lichess", json={"username": "tutee", "max_games": 50}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["imported"], body["skipped"]) == (2, 0)  # the Chess960 game is left out
        request = export.calls.last.request
        assert request.headers["Accept"] == "application/x-chess-pgn"
        assert request.url.params["max"] == "50"
        assert request.url.params["clocks"] == "true" and request.url.params["opening"] == "true"

        res = await aclient.post("/games/import/lichess", json={"username": "tutee"})
        assert res.json()["imported"] == 0 and res.json()["skipped"] == 2

    listing = (await aclient.get("/games", params={"user": "tutee"})).json()
    assert [g["source_id"] for g in listing] == ["Qw34Er56", "Zy98Xw76"]
    assert [g["user_color"] for g in listing] == ["black", "white"]
    assert listing[1]["opening_name"] == "Italian Game"  # header wins over the book lookup
    assert listing[1]["time_control"] == "300+0" and listing[1]["result"] == "1/2-1/2"

    detail = (await aclient.get(f"/games/{listing[1]['id']}")).json()
    assert [m["clock"] for m in detail["moves"]] == [300.0, 300.0, 299.0, 298.0, 297.0, 296.0]

    async with db.session_factory()() as session:
        user = await session.scalar(select(User).where(User.platform == "lichess"))
        assert user is not None and (user.rating_rapid, user.rating_blitz) == (1541, 1490)


async def test_lichess_fetch_sends_token() -> None:
    with respx.mock(assert_all_called=False) as mock:
        export = mock.get(url__regex=r"https://lichess\.org/api/games/user/.*").mock(
            return_value=httpx.Response(200, text=LICHESS_EXPORT)
        )
        games = await importers.lichess_fetch("tutee", 10, token="secret")
    assert len(games) == 2 and games[0].source_id == "Zy98Xw76"
    assert export.calls.last.request.headers["Authorization"] == "Bearer secret"
