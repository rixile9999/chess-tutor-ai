"""The practice opening catalogue: every entry resolves to one TSV line, the derived tabiya,
structure and plans, the user's record, and the book endpoint.

No engine here: the catalogue is pure data plus python-chess.
"""

from __future__ import annotations

import chess
import chess.pgn
from fastapi.testclient import TestClient

from chess_tutor import openings, structure
from chess_tutor.services import openings_catalog as svc
from chess_tutor.services import plans as kb

USERNAME = "tutee"
CARLSBAD = "qgd-exchange-carlsbad"

# The Carlsbad tabiya reached by a different move order, so the record has to be found by
# position and not by the move list: 2...Nf6 and 3...d5 are swapped.
CARLSBAD_TRANSPOSED = "d4 Nf6 c4 e6 Nc3 d5 e3 Be7 cxd5 exd5 Bd3 O-O Nf3 c6".split()


def pgn_text(sans: list[str], *, white: str, black: str, result: str) -> str:
    game = chess.pgn.Game()
    game.headers["Event"] = "test"
    game.headers["Date"] = "2026.09.01"
    game.headers["White"] = white
    game.headers["Black"] = black
    game.headers["Result"] = result
    node: chess.pgn.GameNode = game
    for san in sans:
        node = node.add_variation(node.board().parse_san(san))
    return str(game)


# ---------- the catalogue is real data ----------


def test_ids_and_families_are_well_formed() -> None:
    ids = [entry.id for entry in svc.CATALOG]
    assert len(ids) == len(set(ids)), "catalogue ids must be unique"
    assert 25 <= len(ids) <= 40, f"{len(ids)} entries; the plan calls for about 30"
    for entry in svc.CATALOG:
        assert entry.family in svc.FAMILY_LABELS, entry.id
        assert entry.id == entry.id.lower().strip("-")
        assert " " not in entry.id
        assert entry.sides, entry.id
        assert set(entry.sides) <= {"white", "black"}
    for family in svc.FAMILY_LABELS:
        assert any(e.family == family for e in svc.CATALOG), f"{family} has no entry"


def test_every_entry_resolves_to_exactly_one_tsv_row() -> None:
    for entry in svc.CATALOG:
        rows = openings.find_rows(entry.eco, entry.tsv_name, entry.ply)
        assert len(rows) == 1, f"{entry.id}: {len(rows)} rows for {entry.eco} {entry.tsv_name}"
        assert rows[0].eco == entry.eco and rows[0].name == entry.tsv_name


def test_every_line_is_legal_and_reaches_a_named_tabiya() -> None:
    for r in svc.resolved():
        board = chess.Board()
        for san in r.line_san:
            move = board.parse_san(san)  # raises if the line is not legal
            board.push(move)
        assert board.fen() == r.board.fen()
        assert board.is_valid()
        assert chess.Board(r.board.fen()).is_valid()
        assert 6 <= len(r.moves) <= 20, f"{r.entry.id}: {len(r.moves)} plies"
        assert len(r.fens) == len(r.moves) + 1
        # The tabiya is a book position, so /play/book can name it.
        named = openings.lookup(board)
        assert named is not None and named.name == r.entry.tsv_name, r.entry.id


def test_most_tabiyas_have_a_named_structure() -> None:
    keys = [r.structure.key for r in svc.resolved()]
    classified = [k for k in keys if k != "unclassified"]
    assert len(classified) / len(keys) >= 0.6, keys
    for r in svc.resolved():
        assert r.structure == structure.classify(r.board)
        # Every classified structure the knowledge base knows must yield plans for both sides.
        if r.structure.key in kb.PLANS:
            assert svc.plans_for(r, "white") and svc.plans_for(r, "black"), r.entry.id


def test_the_carlsbad_entry_is_the_carlsbad() -> None:
    r = svc.find(CARLSBAD)
    assert r is not None
    assert r.structure.key == "carlsbad"
    assert list(r.line_san) == "d4 d5 c4 e6 Nc3 Nf6 e3 Be7 cxd5 exd5 Bd3 O-O Nf3 c6".split()
    board = chess.Board()
    for san in CARLSBAD_TRANSPOSED:
        board.push_san(san)
    assert openings.position_key(board) == r.key, "the move order must not matter"


# ---------- cards and detail over HTTP ----------


def test_cards_without_username_have_no_record(client: TestClient) -> None:
    res = client.get("/play/openings")
    assert res.status_code == 200, res.text
    cards = res.json()
    assert len(cards) == len(svc.CATALOG)
    assert all(card["record"] is None for card in cards)
    card = next(c for c in cards if c["id"] == CARLSBAD)
    assert card["family"] == "d4d5" and card["family_label"] == "1.d4 d5"
    assert card["eco"] == "D35" and card["structure"]["key"] == "carlsbad"
    assert card["name"] == "QGD 익스체인지 (칼스바드)"
    assert card["line_san"][:4] == ["d4", "d5", "c4", "e6"]
    assert chess.Board(card["tabiya_fen"]).is_valid()
    assert card["sides"] == ["white", "black"]


def test_unknown_username_gives_empty_records(client: TestClient) -> None:
    cards = client.get("/play/openings", params={"username": "nobody"}).json()
    # A name nobody imported is not an error and must not create an account: empty records.
    assert all(card["record"] is not None for card in cards)
    assert all(card["record"]["games"] == 0 and card["record"]["score"] is None for card in cards)
    assert all(card["record"]["practice_games"] == 0 for card in cards)


def test_record_counts_imported_and_practice_games(client: TestClient) -> None:
    won = pgn_text(CARLSBAD_TRANSPOSED, white=USERNAME, black="rival", result="1-0")
    lost = pgn_text(CARLSBAD_TRANSPOSED, white="rival", black=USERNAME, result="0-1")
    res = client.post("/games/import/pgn", json={"pgn": f"{won}\n\n{lost}", "username": USERNAME})
    assert res.json()["imported"] == 2, res.text

    res = client.post(
        "/play/games",
        json={
            "username": USERNAME,
            "user_color": "white",
            "moves_san": CARLSBAD_TRANSPOSED,
            "result": "0-1",
            "opening_id": CARLSBAD,
            "practice_mode": "tabiya",
            "analyse": False,
        },
    )
    assert res.status_code == 201, res.text

    cards = client.get("/play/openings", params={"username": USERNAME}).json()
    card = next(c for c in cards if c["id"] == CARLSBAD)
    # One win as White, one win as Black: two games, a perfect score.
    assert card["record"]["games"] == 2 and card["record"]["score"] == 1.0
    assert card["record"]["practice_games"] == 1 and card["record"]["practice_score"] == 0.0
    other = next(c for c in cards if c["id"] == "sicilian-najdorf")
    assert other["record"]["games"] == 0 and other["record"]["score"] is None

    detail = client.get(f"/play/openings/{CARLSBAD}", params={"username": USERNAME}).json()
    assert detail["record"] == card["record"]


def test_detail_has_plans_for_both_sides_and_a_fen_per_move(client: TestClient) -> None:
    res = client.get(f"/play/openings/{CARLSBAD}")
    assert res.status_code == 200, res.text
    detail = res.json()
    assert len(detail["fens"]) == len(detail["line_san"]) + 1
    assert detail["fens"][0] == chess.STARTING_FEN
    assert detail["fens"][-1] == detail["tabiya_fen"]
    board = chess.Board()
    for san, fen in zip(detail["line_san"], detail["fens"][1:], strict=True):
        board.push_san(san)
        assert board.fen() == fen
    assert detail["plans_white"] and detail["plans_black"]
    assert {p["side"] for p in detail["plans_white"]} == {"white"}
    assert {p["side"] for p in detail["plans_black"]} == {"black"}
    # No engine ran, so nothing can be a pv_match and nothing was played yet.
    assert {p["status"] for p in detail["plans_white"]} <= {"later", "unavailable"}
    assert any("소수 공격" in p["title"] for p in detail["plans_white"]), detail["plans_white"]
    assert detail["record"] is None


def test_unknown_opening_is_404(client: TestClient) -> None:
    res = client.get("/play/openings/no-such-opening")
    assert res.status_code == 404 and "카탈로그" in res.json()["detail"]


# ---------- the book ----------


def test_book_from_the_start_knows_the_first_moves(client: TestClient) -> None:
    res = client.get("/play/book", params={"fen": chess.STARTING_FEN})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["opening"] is None
    sans = [m["san"] for m in body["moves"]]
    assert {"e4", "d4", "c4", "Nf3"} <= set(sans)
    assert len(sans) == len(set(sans)), "one entry per move"
    assert [m["name"] for m in body["moves"]] == sorted(m["name"] for m in body["moves"])
    board = chess.Board()
    for move in body["moves"]:
        assert chess.Move.from_uci(move["uci"]) in board.legal_moves
        assert board.san(chess.Move.from_uci(move["uci"])) == move["san"]
        assert move["eco"] and move["name"]


def test_book_names_the_carlsbad_tabiya(client: TestClient) -> None:
    r = svc.find(CARLSBAD)
    assert r is not None
    body = client.get("/play/book", params={"fen": r.board.fen()}).json()
    assert body["opening"]["name"] == "퀸즈 갬빗 디클라인드: 익스체인지 변화"
    assert body["opening"]["eco"] == "D35"
    assert body["opening"]["san"] == "" and body["opening"]["uci"] == ""


def test_book_follows_transpositions(client: TestClient) -> None:
    board = chess.Board()
    for san in "d4 Nf6 c4 e6 Nc3 d5".split():
        board.push_san(san)
    body = client.get("/play/book", params={"fen": board.fen()}).json()
    assert body["moves"], "the book must find this position by move order transposition"
    assert "cxd5" in [m["san"] for m in body["moves"]]


def test_book_rejects_a_bad_fen(client: TestClient) -> None:
    assert client.get("/play/book", params={"fen": "not a fen at all"}).status_code == 422
    # Syntactically fine, but two kings short of a legal position.
    assert (
        client.get("/play/book", params={"fen": "8/8/8/8/8/8/8/KK6 w - - 0 1"}).status_code == 422
    )
