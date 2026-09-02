"""Review orchestration: the mockup game (20...Qd7?? 21.Nxf6+) through the /review endpoints
at depth 8, the per-(game, ply, rating) cache, and the pure helpers that label branches,
write the 'why not X' note and draw arrows."""

from __future__ import annotations

from collections.abc import Iterator

import chess
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from chess_tutor import db, models, schemas
from chess_tutor.config import get_settings
from chess_tutor.engine import find_stockfish
from chess_tutor.services import review as review_svc

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")

FEN = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
AFTER = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 5 21"
PUNISHED = "5rk1/p2qbppp/1p2pN2/8/4P3/4B3/PP2QPPP/3R2K1 b - - 0 21"
PGN = (
    '[Event "mockup"]\n[White "a"]\n[Black "b"]\n[Result "1-0"]\n[SetUp "1"]\n'
    f'[FEN "{FEN}"]\n\n20... Qd7 21. Nxf6+ Bxf6 22. Rxd7 1-0'
)
DEPTH = 8


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    yield


async def _insert_game() -> int:
    async with db.session_factory()() as session:
        game = models.Game(
            source="pgn", source_id="mockup", pgn=PGN, white="a", black="b", result="1-0"
        )
        session.add(game)
        await session.commit()
        return game.id


def _line(fen: str, sans: str, cp: int | None = None, mate: int | None = None, rank: int = 1):
    board = chess.Board(fen)
    ucis = []
    for san in sans.split():
        move = board.parse_san(san)
        ucis.append(move.uci())
        board.push(move)
    return schemas.EngineLine(
        rank=rank, score=schemas.Score(cp=cp, mate=mate), pv=sans.split(), pv_uci=ucis
    )


# ---------- pure helpers ----------


def test_branch_labels_from_material_and_mate() -> None:
    board = chess.Board(PUNISHED)  # Black to move after 21.Nxf6+
    black = chess.BLACK
    lines = [
        _line(PUNISHED, "gxf6 Rxd7 Bc5 Rd1", cp=590, rank=1),
        _line(PUNISHED, "Bxf6 Rxd7 Rd8 Rxd8+ Bxd8", cp=630, rank=2),
        _line(PUNISHED, "Kh8 Nxd7 Rd8", cp=770, rank=3),
    ]
    branches = review_svc.branches_from(board, lines, black)
    assert [b.moves for b in branches] == [
        ["gxf6", "Rxd7", "Bc5"],
        ["Bxf6", "Rxd7", "Rd8"],
        ["Kh8", "Nxd7", "Rd8"],
    ]
    assert [b.result for b in branches] == ["퀸 상실", "같은 결과", "같은 결과"]
    assert branches[0].eval == schemas.Score(cp=590)

    # mate in the line, or a mate score, reads 메이트
    mate_fen = "6k1/5ppp/8/8/8/8/5PPP/R5K1 b - - 0 1"
    mate_board = chess.Board(mate_fen)
    moves = [mate_board.parse_san("h6"), chess.Move.from_uci("a1a8")]
    assert review_svc.outcome_label(mate_board, moves, schemas.Score(mate=1), black) == "메이트"
    # a quiet line with no material change is labelled by the evaluation
    quiet = chess.Board(PUNISHED)
    kh8 = [quiet.parse_san("Kh8")]
    assert review_svc.outcome_label(quiet, kh8, schemas.Score(cp=-350), black) == "큰 손실 없음"
    assert review_svc.outcome_label(quiet, kh8, schemas.Score(cp=350), black) == "결정적 열세"
    assert review_svc.outcome_label(quiet, kh8, schemas.Score(cp=150), black) == "열세"


def test_note_needs_a_clearly_worse_second_line() -> None:
    board = chess.Board(AFTER)
    main = _line(AFTER, "Nxf6+ Bxf6 Rxd7", cp=540, rank=1)
    second = _line(AFTER, "Nxe7+ Qxe7 Bf4", cp=70, rank=2)
    note, line = review_svc.note_from(board, [main, second], chess.WHITE)
    assert note is not None and note.startswith(
        "왜 Nxe7+이 아니라 Nxf6+인가: Nxe7+에는 Qxe7이 있습니다."
    )
    assert "+0.7" in note and "+5.4" in note
    assert line == ["Nxe7+", "Qxe7"]
    close = _line(AFTER, "Nxe7+ Qxe7 Bf4", cp=500, rank=2)
    assert review_svc.note_from(board, [main, close], chess.WHITE) == (None, [])
    assert review_svc.note_from(board, [main], chess.WHITE) == (None, [])


def test_arrows_mark_played_punishing_discovery_and_best() -> None:
    move = schemas.MoveAnalysis(
        ply=1,
        san="Qd7",
        uci="c6d7",
        color="black",
        fen_before=FEN,
        fen_after=AFTER,
        eval_before=schemas.Score(cp=40),
        eval_after=schemas.Score(cp=560),
        best_move_san="Nxd5",
        best_move_uci="f6d5",
        classification="blunder",
    )
    refutation = schemas.Refutation(
        main_line=["Nxf6+", "Bxf6", "Rxd7"],
        motifs=[
            schemas.MotifOut(
                kind="discovered_attack",
                mover="f6",
                attacker="d1",
                targets=["d7"],
                with_check=True,
                safe=False,
            )
        ],
    )
    arrows = review_svc.arrows_of(move, refutation, [_line(FEN, "Nxd5 exd5", cp=30)])
    assert [(a.orig, a.dest, a.color, a.dashed) for a in arrows] == [
        ("c6", "d7", "ink", False),
        ("d5", "f6", "bad", False),
        ("d1", "d7", "ink", True),
        ("f6", "d5", "good", False),
    ]


# ---------- endpoints (engine) ----------


@needs_engine
async def test_review_of_the_mockup_blunder(aclient: AsyncClient) -> None:
    game_id = await _insert_game()
    res = await aclient.get(f"/review/{game_id}/1", params={"depth": DEPTH, "rating": 1500})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["san"] == "Qd7" and body["color"] == "black" and body["ply"] == 1
    assert body["classification"] == "blunder"
    assert body["fen_before"] == FEN and body["fen_after"] == AFTER
    assert body["eval_after"]["cp"] > body["eval_before"]["cp"] + 300

    refutation = body["refutation"]
    assert refutation["main_line"][0] == "Nxf6+"
    kinds = {m["kind"] for m in refutation["motifs"]}
    assert "discovered_attack" in kinds
    assert len(refutation["branches"]) == 3
    assert {b["moves"][0] for b in refutation["branches"]} == {"Bxf6", "gxf6", "Kh8"}
    assert all(2 <= len(b["moves"]) <= 3 for b in refutation["branches"])
    assert refutation["branches"][0]["result"] == "퀸 상실"
    assert {b["result"] for b in refutation["branches"][1:]} == {"같은 결과"}
    assert refutation["note"] is not None and refutation["note"].startswith(
        "왜 Nxe7+이 아니라 Nxf6+인가"
    )

    alternatives = body["alternatives"]
    assert len(alternatives) == 2 and alternatives[0]["is_best"] and not alternatives[1]["is_best"]
    assert {a["san"] for a in alternatives} <= {"Nxd5", "exd5", "Bd8"}
    assert all(a["why"].endswith(".") for a in alternatives)
    assert body["comparison"]["a_san"] == alternatives[0]["san"]
    assert body["comparison"]["b_san"] == "Qd7"  # the played move is not in the top two

    explanation = body["explanation"]
    assert explanation["source"] == "template"
    assert explanation["headline"] == "20… Qd7 블런더"
    assert explanation["verified"] is True
    assert explanation["total_claims"] > 0
    assert explanation["verified_claims"] == explanation["total_claims"]
    assert "Rd1" in explanation["lead"]
    assert any("Nxf6+" in s for s in explanation["sentences"])
    for text in [explanation["headline"], explanation["lead"], *explanation["sentences"]]:
        assert "—" not in text

    assert body["highlights"] == ["c6", "d7"]
    arrows = {(a["orig"], a["dest"], a["color"], a["dashed"]) for a in body["arrows"]}
    assert ("c6", "d7", "ink", False) in arrows
    assert ("d5", "f6", "bad", False) in arrows
    assert ("d1", "d7", "ink", True) in arrows
    assert any(a[2] == "good" for a in arrows)

    human = body["human"]
    assert human is not None and human["rating"] == 1500
    assert human["natural_reason"] == "Nd5가 e7 비숍을 공격하자 퀸으로 지켰습니다."
    assert human["played_prob"] is not None
    strategy = body["strategy"]
    assert strategy is not None and strategy["structure"]["key"] == "open_center"
    assert strategy["your_move"]["san"] == "Qd7"


@needs_engine
async def test_move_list_cache_and_errors(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = await _insert_game()
    assert (await aclient.get(f"/review/{game_id + 50}")).status_code == 404
    assert (await aclient.get(f"/review/{game_id + 50}/1")).status_code == 404

    res = await aclient.get(f"/review/{game_id}", params={"depth": DEPTH})
    assert res.status_code == 200
    items = res.json()
    assert [i["ply"] for i in items] == [1, 2, 3, 4]
    assert [i["san"] for i in items] == ["Qd7", "Nxf6+", "Bxf6", "Rxd7"]
    assert items[0]["classification"] == "blunder" and items[0]["color"] == "black"
    assert items[1]["classification"] == "best"

    assert (await aclient.get(f"/review/{game_id}/0")).status_code == 404
    assert (await aclient.get(f"/review/{game_id}/5")).status_code == 404

    # a best move: no refutation, best versus second-best comparison, verified prose
    res = await aclient.get(f"/review/{game_id}/2", params={"rating": 1500})
    assert res.status_code == 200
    body = res.json()
    assert body["classification"] == "best" and body["refutation"] is None
    assert body["alternatives"][0]["san"] == "Nxf6+" and body["alternatives"][0]["is_best"]
    assert body["comparison"]["a_san"] == "Nxf6+" and body["comparison"]["b_san"] != "Nxf6+"
    assert body["explanation"]["headline"] == "21. Nxf6+ 최선"
    assert body["explanation"]["verified"] is True
    assert body["motifs"] and {m["kind"] for m in body["motifs"]} >= {"discovered_attack", "fork"}
    assert body["highlights"] == ["d5", "f6"]

    # cached per (game, ply, rating): the same rating is served from the row, a new one is
    # rebuilt and replaces it
    calls: list[int] = []
    original = review_svc.compute_move_review

    async def counting(session, game, analysis, ply, rating):  # type: ignore[no-untyped-def]
        calls.append(rating)
        return await original(session, game, analysis, ply, rating)

    monkeypatch.setattr(review_svc, "compute_move_review", counting)
    again = await aclient.get(f"/review/{game_id}/2", params={"rating": 1500})
    assert again.status_code == 200 and again.json() == body and calls == []
    other = await aclient.get(f"/review/{game_id}/2", params={"rating": 1200})
    assert other.status_code == 200 and calls == [1200]
    assert other.json()["human"]["rating"] == 1200
    async with db.session_factory()() as session:
        rows = (await session.execute(select(models.MoveReview))).scalars().all()
    assert {(r.game_id, r.ply) for r in rows} == {(game_id, 2)}
    assert all(r.verified for r in rows)
