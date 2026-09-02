"""Opening map, piece heatmap and pawn-break timing over six short synthetic games.

Two Sicilian move orders (Najdorf 2...d6 and O'Kelly 2...a6) transpose at ply 10; two games
leave the book with 6...Qc7. The user is Black in every game.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import chess
import httpx
import pytest
import respx
from httpx import AsyncClient

from chess_tutor import db
from chess_tutor.models import Game, User
from chess_tutor.openings import position_key
from chess_tutor.services import openings_map as om

# (moves, result). Results are 2 wins, 1 draw, 3 losses for Black.
GAMES: list[tuple[str, str]] = [
    # Najdorf order, English Attack, ...b5 at move 11, g4 at move 11, f5 at move 15.
    (
        "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5 Nb3 Be6 f3 Be7 Qd2 O-O O-O-O Nbd7 "
        "g4 b5 g5 b4 Ne2 Ne8 f4 a5 f5 a4 Nbd4 exd4 Nxd4 b3",
        "0-1",
    ),
    (
        "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 e5 Nb3 Be6 f3 Be7 Qd2 O-O O-O-O Nbd7 "
        "g4 b5 h4 Nb6 Nd5 Nbxd5 exd5 Bf5",
        "1-0",
    ),
    # O'Kelly order transposing into the same Najdorf position; ...d5 at move 11 / 13.
    (
        "e4 c5 Nf3 a6 d4 cxd4 Nxd4 Nf6 Nc3 d6 Be3 e5 Nb3 Be6 f3 Be7 Qd2 O-O O-O-O Nbd7 "
        "g4 d5 exd5 Nxd5 Nxd5 Bxd5 Qxd5 Nf6 Qd2 Qc7 Kb1 Rfd8",
        "0-1",
    ),
    (
        "e4 c5 Nf3 a6 d4 cxd4 Nxd4 Nf6 Nc3 d6 Be3 e5 Nb3 Be6 f3 Be7 Qd2 O-O O-O-O Nbd7 "
        "g4 b5 g5 Ne8 h4 d5 exd5 Bxd5 Nxd5 Qc7",
        "1/2-1/2",
    ),
    # Deviation: 6...Qc7 is not in the book and the game never re-enters it.
    (
        "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 Qc7 f3 e5 Nb3 Be6 Qd2 Be7 O-O-O b5 "
        "g4 O-O g5 Nfd7 h4 b4 Ne2 a5 Kb1 a4",
        "1-0",
    ),
    (
        "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6 Be3 Qc7 f3 e5 Nb3 Be6 Qd2 Be7 O-O-O O-O "
        "g4 b5 g5 Nfd7 h4 b4 Nd5 Bxd5 exd5 a5",
        "1-0",
    ),
]

NAJDORF = "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6".split()


def pgn_text(sans: str, result: str) -> str:
    """Build a PGN movetext, checking every move is legal with python-chess."""
    board = chess.Board()
    parts: list[str] = []
    for i, san in enumerate(sans.split()):
        if i % 2 == 0:
            parts.append(f"{i // 2 + 1}.")
        parts.append(san)
        board.push_san(san)
    return " ".join(parts) + " " + result


def game_rows(user_color: str = "black") -> list[SimpleNamespace]:
    return [
        SimpleNamespace(pgn=pgn_text(sans, result), user_color=user_color, result=result)
        for sans, result in GAMES
    ]


def key_after(sans: list[str]) -> str:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return position_key(board)


@pytest.fixture(autouse=True)
def _no_explorer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the Lichess explorer unless a test opts in."""
    monkeypatch.setattr(om, "_explorer_token", lambda: None)
    om._master_cache.clear()


# ---------- build_map ----------


def test_transpositions_merge_into_one_node() -> None:
    m = om.build_map(game_rows(), "black", depth=12, min_games=2)
    assert m.total_games == 6
    assert m.root == position_key(chess.Board())
    # 1 root + 3 shared plies + 2 paths x 6 plies + merged ply 10 + ply 11 + 2 at ply 12.
    assert len(m.nodes) == 20
    assert len(m.edges) == 20
    merged = next(n for n in m.nodes if n.id == key_after(NAJDORF))
    assert merged.depth == 10 and merged.games == 6
    assert merged.eco == "B90" and "Najdorf" in (merged.name or "")
    sources = {e.source for e in m.edges if e.target == merged.id}
    assert len(sources) == 2
    assert merged.is_tabiya


def test_scores_are_from_the_users_perspective() -> None:
    m = om.build_map(game_rows(), "black")
    by_id = {n.id: n for n in m.nodes}
    root = by_id[m.root]
    assert (root.wins, root.draws, root.losses) == (2, 1, 3)
    assert root.score == pytest.approx(2.5 / 6, abs=1e-3)
    okelly = by_id[key_after(["e4", "c5", "Nf3", "a6"])]
    assert okelly.label == "2…a6" and okelly.games == 2
    assert okelly.score == pytest.approx(0.75)
    assert okelly.eco == "B28"
    edge = next(e for e in m.edges if e.target == okelly.id)
    assert edge.san == "a6" and edge.games == 2 and edge.score == pytest.approx(0.75)
    # Same games seen as White flip the record.
    as_white = om.build_map(game_rows("white"), "white")
    root_w = next(n for n in as_white.nodes if n.id == as_white.root)
    assert (root_w.wins, root_w.draws, root_w.losses) == (3, 1, 2)


def test_deviation_is_the_users_first_move_out_of_book() -> None:
    m = om.build_map(game_rows(), "black")
    deviations = [n for n in m.nodes if n.is_deviation]
    assert len(deviations) == 1
    dev = deviations[0]
    assert dev.san == "Qc7" and dev.depth == 12 and dev.games == 2
    assert dev.label == "6…Qc7 책 이탈"
    assert dev.score == 0.0 and dev.name is None
    # 6...e5 is also unnamed, but the games re-enter the book at 8.f3, so it is not a deviation.
    e5 = next(n for n in m.nodes if n.depth == 12 and n.san == "e5")
    assert not e5.is_deviation and e5.games == 4


def test_min_games_prunes_thin_branches_but_keeps_root() -> None:
    m = om.build_map(game_rows(), "black", min_games=3)
    assert len(m.nodes) == 13
    assert m.root in {n.id for n in m.nodes}
    assert not any(n.san == "a6" and n.depth == 4 for n in m.nodes)
    assert not any(n.is_deviation for n in m.nodes)
    merged = next(n for n in m.nodes if n.id == key_after(NAJDORF))
    assert merged.games == 6 and merged.is_tabiya
    ids = {n.id for n in m.nodes}
    assert all(e.source in ids and e.target in ids for e in m.edges)


def test_depth_limits_the_map_and_other_colours_are_ignored() -> None:
    rows = game_rows() + game_rows("white")
    m = om.build_map(rows, "black", depth=4)
    assert m.total_games == 6
    assert max(n.depth for n in m.nodes) == 4
    assert len(m.nodes) == 6  # root, e4, c5, Nf3, d6, a6


def test_unreadable_pgn_is_skipped() -> None:
    rows = game_rows() + [
        SimpleNamespace(pgn="1. e4 e5 2. Ke2 Ke7?? garbage", user_color="black", result="*")
    ]
    rows.append(SimpleNamespace(pgn="", user_color="black", result="1-0"))
    m = om.build_map(rows, "black")
    # The garbled game still parses up to its first illegal move; the empty one is dropped.
    assert m.total_games == 7
    root = next(n for n in m.nodes if n.id == m.root)
    assert root.games == 7 and root.wins + root.draws + root.losses == 6


@respx.mock
def test_master_moves_overlay_from_lichess_explorer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(om, "_explorer_token", lambda: "tok")
    route = respx.get(om.EXPLORER_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "moves": [
                    {"uci": "e2e4", "san": "e4", "white": 500, "draws": 300, "black": 200},
                    {"uci": "d2d4", "san": "d4", "white": 100, "draws": 100, "black": 200},
                ]
            },
        )
    )
    m = om.build_map(game_rows(), "black")
    assert route.called
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"
    master_edges = [e for e in m.edges if e.master_only]
    assert master_edges and all(e.games == 0 for e in master_edges)
    d4 = next(e for e in master_edges if e.source == m.root)
    assert d4.san == "d4" and d4.score == pytest.approx(0.625)  # black: (200 + 50) / 400
    d4_node = next(n for n in m.nodes if n.id == d4.target)
    assert d4_node.master_only and d4_node.label == "1.d4" and d4_node.eco == "A40"
    # 1.e4 is already in the user's map: not duplicated.
    assert sum(1 for e in m.edges if e.source == m.root and e.san == "e4") == 1
    calls = route.call_count
    om.build_map(game_rows(), "black")
    assert route.call_count == calls  # cached


@respx.mock
def test_explorer_errors_are_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(om, "_explorer_token", lambda: "tok")
    respx.get(om.EXPLORER_URL).mock(return_value=httpx.Response(500))
    m = om.build_map(game_rows(), "black")
    assert len(m.nodes) == 20 and not any(e.master_only for e in m.edges)


# ---------- piece_heatmap ----------


def test_heatmap_sums_to_one_and_tracks_captures() -> None:
    h = om.piece_heatmap(game_rows(), "black", "bc8", through_move=15)
    assert h.piece == "흑 c8 비숍" and h.games == 6 and h.through_move == 15
    assert sum(h.squares.values()) == pytest.approx(1.0)
    # Taken on d5 in three games, still on e6 in two, moved to f5 in one.
    assert h.squares["d5"] == pytest.approx(0.5)
    assert h.squares["e6"] == pytest.approx(1 / 3, abs=1e-3)
    assert h.squares["f5"] == pytest.approx(1 / 6, abs=1e-3)


def test_heatmap_respects_through_move_and_castling() -> None:
    early = om.piece_heatmap(game_rows(), "black", "bc8", through_move=6)
    assert early.squares == {"c8": 1.0}
    bishop = om.piece_heatmap(game_rows(), "black", "bf8")
    assert bishop.squares == {"e7": 1.0}
    rook = om.piece_heatmap(game_rows(), "black", "wa1")  # every game had O-O-O
    assert rook.piece == "백 a1 룩" and rook.squares == {"d1": 1.0}


def test_heatmap_rejects_bad_piece_spec() -> None:
    for bad in ("bf4", "xf8", "f8", "bz9"):
        with pytest.raises(ValueError):
            om.piece_heatmap(game_rows(), "black", bad)


def test_track_piece_en_passant() -> None:
    board = chess.Board()
    moves = [board.push_san(s) for s in ["e4", "a6", "e5", "d5", "exd6"]]
    assert om.track_piece(moves, chess.D7, 15) == chess.D5


# ---------- break_timing ----------


def test_break_histograms() -> None:
    timings = om.break_timing(game_rows(), "black")
    assert len(timings) == len(om.BREAKS) == 11
    assert all(len(t.histogram) == 21 for t in timings)
    assert all(t.from_move == 10 and t.to_move == 30 and t.master_median is None for t in timings)
    by_label = {t.label: t for t in timings}
    d5 = by_label["…d5"]
    assert d5.side == "black" and sum(d5.histogram) == 2
    assert d5.histogram[11 - 10] == 1 and d5.histogram[13 - 10] == 1
    assert d5.my_avg == pytest.approx(12.0)
    b5 = by_label["…b5"]
    assert b5.histogram[0] == 1 and b5.histogram[1] == 4 and b5.my_avg == pytest.approx(10.8)
    # 6...e5 and 1...c5 happen before move 10 and are outside the window.
    assert sum(by_label["…e5"].histogram) == 0 and by_label["…e5"].my_avg is None
    g4 = by_label["g2-g4"]
    assert g4.side == "white" and g4.histogram[1] == 6 and g4.my_avg is None
    assert by_label["f4-f5"].histogram[15 - 10] == 1
    assert sum(by_label["e4-e5"].histogram) == 0


def test_break_structure_filter_uses_classifier_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = types.ModuleType("chess_tutor.structure")

    def classify(board: chess.Board) -> SimpleNamespace:
        queen_c7 = board.piece_at(chess.C7) == chess.Piece(chess.QUEEN, chess.BLACK)
        return SimpleNamespace(key="qc7" if queen_c7 else "other", name="x", confidence=1.0)

    fake.classify = classify  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "chess_tutor.structure", fake)
    by_label = {t.label: t for t in om.break_timing(game_rows(), "black", structure="qc7")}
    # Only the two 6...Qc7 games: ...b5 at moves 10 and 11, no ...d5 at all.
    assert by_label["…b5"].histogram[0] == 1 and by_label["…b5"].histogram[1] == 1
    assert sum(by_label["…d5"].histogram) == 0
    monkeypatch.delitem(sys.modules, "chess_tutor.structure")
    monkeypatch.setattr(om, "_structure_filter", lambda structure: None)
    unfiltered = {t.label: t for t in om.break_timing(game_rows(), "black", structure="qc7")}
    assert sum(unfiltered["…d5"].histogram) == 2


# ---------- HTTP ----------


async def seed(username: str = "tester", color: str = "black") -> None:
    async with db.session_factory()() as session:
        user = User(username=username, platform="chesscom")
        session.add(user)
        await session.flush()
        for i, (sans, result) in enumerate(GAMES):
            session.add(
                Game(
                    user_id=user.id,
                    source="pgn",
                    source_id=f"{username}-{i}",
                    pgn=pgn_text(sans, result),
                    white="opp" if color == "black" else username,
                    black=username if color == "black" else "opp",
                    result=result,
                    user_color=color,
                )
            )
        await session.commit()


async def test_map_endpoint(aclient: AsyncClient) -> None:
    await seed()
    res = await aclient.get("/openings/map", params={"username": "tester", "color": "black"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_games"] == 6 and body["color"] == "black"
    assert len(body["nodes"]) == 20 and len(body["edges"]) == 20
    assert any(n["is_deviation"] for n in body["nodes"])
    res = await aclient.get(
        "/openings/map",
        params={"username": "tester", "color": "black", "depth": 4, "min_games": 3},
    )
    assert res.status_code == 200 and len(res.json()["nodes"]) == 5


async def test_map_404_without_games(aclient: AsyncClient) -> None:
    await seed()
    res = await aclient.get("/openings/map", params={"username": "nobody", "color": "black"})
    assert res.status_code == 404
    assert "기보가 없습니다" in res.json()["detail"]
    res = await aclient.get("/openings/map", params={"username": "tester", "color": "white"})
    assert res.status_code == 404
    res = await aclient.get("/openings/map", params={"username": "tester", "color": "red"})
    assert res.status_code == 422


async def test_heatmap_endpoint(aclient: AsyncClient) -> None:
    await seed()
    res = await aclient.get(
        "/openings/heatmap",
        params={"username": "tester", "color": "black", "piece": "bc8", "through_move": 15},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["games"] == 6 and sum(body["squares"].values()) == pytest.approx(1.0)
    res = await aclient.get(
        "/openings/heatmap", params={"username": "tester", "color": "black", "piece": "bf4"}
    )
    assert res.status_code == 422


async def test_breaks_endpoint(aclient: AsyncClient) -> None:
    await seed()
    res = await aclient.get("/openings/breaks", params={"username": "tester", "color": "black"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 11 and all(len(t["histogram"]) == 21 for t in body)
    d5 = next(t for t in body if t["label"] == "…d5")
    assert d5["my_avg"] == pytest.approx(12.0)
    # An unknown structure is ignored while the classifier module does not exist.
    res = await aclient.get(
        "/openings/breaks", params={"username": "tester", "color": "black", "structure": "iqp"}
    )
    assert res.status_code == 200 and len(res.json()) == 11
