"""Puzzles cut from a game's stored analysis and the SM-2 schedule behind /training.

The analysis JSON is fabricated here with the pydantic models from schemas.py; every fen and
line in it is replayed with python-chess so the puzzles are checked against real chess."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import chess
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from chess_tutor import db
from chess_tutor.models import Analysis, Game, Puzzle, PuzzleAttempt, User
from chess_tutor.schemas import Classification, EngineLine, MoveAnalysis, Score
from chess_tutor.services import analysis as analysis_svc
from chess_tutor.services import profile as profile_svc
from chess_tutor.services import puzzles as svc
from chess_tutor.services import users as user_svc

# Black grabs a2 with the rook that guarded the back rank; White mates in two.
BACK_RANK_BEFORE = "r2q2k1/1p3ppp/8/8/8/8/P3QPPP/4R1K1 b - - 0 30"
BACK_RANK_MATE = ["Qe8+", "Qxe8", "Rxe8#"]
# The mockup's 20...Qd7?? : Nxf6+ uncovers the d-file rook on the queen (tests/test_api.py).
QD7_BEFORE = "3q1rk1/p3bppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 0 20"
QD7_PUNISH = ["Nxf6+", "Bxf6", "Rxd7", "Rfd8"]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _line(board: chess.Board, sans: list[str], score: Score) -> EngineLine:
    """Engine line from SAN; parse_san raises on an illegal move."""
    scratch = board.copy()
    ucis: list[str] = []
    for san in sans:
        move = scratch.parse_san(san)
        ucis.append(move.uci())
        scratch.push(move)
    return EngineLine(rank=1, score=score, pv=list(sans), pv_uci=ucis)


def _move(
    board: chess.Board,
    san: str,
    *,
    ply: int,
    classification: Classification,
    eval_before: Score,
    eval_after: Score,
    lines: list[EngineLine],
) -> MoveAnalysis:
    move = board.parse_san(san)
    after = board.copy()
    after.push(move)
    return MoveAnalysis(
        ply=ply,
        san=san,
        uci=move.uci(),
        color="white" if board.turn == chess.WHITE else "black",
        fen_before=board.fen(),
        fen_after=after.fen(),
        eval_before=eval_before,
        eval_after=eval_after,
        classification=classification,
        lines=lines,
    )


def error_and_reply(
    fen_before: str,
    error_san: str,
    punish_sans: list[str],
    *,
    ply: int,
    eval_before: Score,
    eval_after: Score,
    classification: Classification = "blunder",
) -> list[MoveAnalysis]:
    """Two analysed plies: the error, then the reply whose lines[0] is the punishment."""
    board = chess.Board(fen_before)
    error = _move(
        board,
        error_san,
        ply=ply,
        classification=classification,
        eval_before=eval_before,
        eval_after=eval_after,
        lines=[],
    )
    after = chess.Board(error.fen_after)
    best = _line(after, punish_sans, eval_after)
    reply = _move(
        after,
        punish_sans[0],
        ply=ply + 1,
        classification="best",
        eval_before=eval_after,
        eval_after=eval_after,
        lines=[best],
    )
    return [error, reply]


def back_rank_moves() -> list[MoveAnalysis]:
    return error_and_reply(
        BACK_RANK_BEFORE,
        "Rxa2",
        BACK_RANK_MATE,
        ply=60,
        eval_before=Score(cp=30),
        eval_after=Score(mate=2),
    )


def qd7_moves() -> list[MoveAnalysis]:
    return error_and_reply(
        QD7_BEFORE, "Qd7", QD7_PUNISH, ply=40, eval_before=Score(cp=40), eval_after=Score(cp=900)
    )


def replay(fen: str, ucis: list[str]) -> chess.Board:
    board = chess.Board(fen)
    for uci in ucis:
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, f"{uci} illegal in {board.fen()}"
        board.push(move)
    return board


async def store_game(
    moves: list[MoveAnalysis],
    status: str = "done",
    user_id=None,
    user_color: str | None = None,
) -> int:
    async with db.session_factory()() as session:
        game = Game(
            source="pgn", source_id=f"g-{moves[0].ply}", pgn="*", white="w", black="b", result="*"
        )
        game.user_id = user_id
        game.user_color = user_color
        session.add(game)
        await session.flush()
        session.add(
            Analysis(
                game_id=game.id,
                status=status,
                moves=[m.model_dump(mode="json") for m in moves],
            )
        )
        await session.commit()
        return game.id


async def make_user(username: str, platform: str = "chesscom") -> int:
    """Accounts are made by an import, never by a query parameter, so tests make their own."""
    async with db.session_factory()() as session:
        user = User(username=username, platform=platform)
        session.add(user)
        await session.commit()
        return user.id


def stored_lines(*move_lists: list[MoveAnalysis]) -> dict[str, list[EngineLine]]:
    """What a deeper engine would answer if it still agreed with the stored analysis."""
    return {m.fen_before: list(m.lines) for moves in move_lists for m in moves if m.lines}


def patch_engine(
    monkeypatch: pytest.MonkeyPatch, lines_by_fen: dict[str, list[EngineLine]]
) -> list[tuple[str, int | None]]:
    """Stand in for the verification engine; returns the (fen, depth) it was asked about."""
    calls: list[tuple[str, int | None]] = []

    async def get_lines(
        fen: str, depth: int | None = None, multipv: int | None = None
    ) -> list[EngineLine]:
        calls.append((fen, depth))
        return lines_by_fen.get(fen, [])

    monkeypatch.setattr(analysis_svc, "get_lines", get_lines)
    return calls


# ---------- cutting puzzles (pure) ----------


def test_back_rank_blunder_becomes_mate_puzzle() -> None:
    error, reply = back_rank_moves()
    puzzle = svc.cut_puzzle(error, reply)
    assert puzzle is not None
    assert puzzle.fen == error.fen_after
    assert svc.orientation_of(puzzle.fen) == "white"
    assert puzzle.solution == ["e2e8", "d8e8", "e1e8"]
    # Qe8+ hits Qd8 and Kg8, but Black's only reply takes the queen: it is a deflection
    # sacrifice, not a fork, and no detector claims a motif for it.
    assert puzzle.motif is None
    assert puzzle.ply == 60
    assert replay(puzzle.fen, puzzle.solution).is_checkmate()


def test_hanging_queen_puzzle_ends_on_the_solvers_move() -> None:
    error, reply = qd7_moves()
    puzzle = svc.cut_puzzle(error, reply)
    assert puzzle is not None
    assert puzzle.solution == ["d5f6", "e7f6", "d1d7"]  # 4-ply line trimmed to White's move
    assert puzzle.motif == "discovered_attack"
    end = replay(puzzle.fen, puzzle.solution)
    assert not end.pieces(chess.QUEEN, chess.BLACK)
    assert end.piece_at(chess.D7) == chess.Piece(chess.ROOK, chess.WHITE)


def test_black_solver_gets_black_orientation() -> None:
    board = chess.Board(BACK_RANK_BEFORE).mirror()  # White errs with Rxa7, Black mates
    error, reply = error_and_reply(
        board.fen(),
        "Rxa7",
        ["Qe1+", "Qxe1", "Rxe1#"],
        ply=59,
        eval_before=Score(cp=-30),
        eval_after=Score(mate=-2),
    )
    puzzle = svc.cut_puzzle(error, reply)
    assert puzzle is not None
    assert svc.orientation_of(puzzle.fen) == "black"
    assert puzzle.solution == ["e7e1", "d1e1", "e8e1"]
    assert puzzle.motif is None  # mirrored deflection sacrifice, see the test above
    assert replay(puzzle.fen, puzzle.solution).is_checkmate()


def test_unconcrete_punishments_are_skipped() -> None:
    small_swing = error_and_reply(
        QD7_BEFORE, "Qd7", QD7_PUNISH, ply=40, eval_before=Score(cp=40), eval_after=Score(cp=180)
    )
    assert svc.cut_puzzle(*small_swing) is None  # 140 cp is under the 150 cp bar

    one_ply = error_and_reply(
        BACK_RANK_BEFORE,
        "Rxa2",
        ["Qe8+"],
        ply=60,
        eval_before=Score(cp=30),
        eval_after=Score(mate=2),
    )
    assert svc.cut_puzzle(*one_ply) is None  # the line must show the reply

    mate_against_solver = error_and_reply(
        BACK_RANK_BEFORE,
        "Rxa2",
        BACK_RANK_MATE,
        ply=60,
        eval_before=Score(cp=30),
        eval_after=Score(mate=-3),
    )
    assert svc.cut_puzzle(*mate_against_solver) is None

    inaccuracy = error_and_reply(
        BACK_RANK_BEFORE,
        "Rxa2",
        BACK_RANK_MATE,
        ply=60,
        eval_before=Score(cp=30),
        eval_after=Score(mate=2),
        classification="inaccuracy",
    )
    assert svc.cut_puzzle(*inaccuracy) is None


def test_mistake_with_big_swing_counts_and_illegal_tail_is_dropped() -> None:
    error, reply = error_and_reply(
        QD7_BEFORE,
        "Qd7",
        QD7_PUNISH,
        ply=40,
        eval_before=Score(cp=40),
        eval_after=Score(cp=400),
        classification="mistake",
    )
    reply.lines[0].pv_uci[2] = "d1d8"  # corrupt the third ply: the rook cannot jump the queen
    puzzle = svc.cut_puzzle(error, reply)
    assert puzzle is not None
    assert puzzle.solution == ["d5f6"]  # legal prefix Nxf6+ Bxf6, trimmed to White's move


def test_cut_puzzles_needs_the_following_ply() -> None:
    error, _reply = back_rank_moves()
    assert svc.cut_puzzles([error]) == []
    assert len(svc.cut_puzzles(back_rank_moves() + qd7_moves())) == 2


def test_only_the_opponents_errors_become_puzzles() -> None:
    """The solver punishes the error, so a puzzle cut from the owner's own blunder would hand
    them the other colour and ask them to punish themselves."""
    moves = back_rank_moves()  # Black errs with Rxa2, White punishes
    assert moves[0].color == "black"

    assert svc.cut_puzzles(moves, user_color="black") == []
    (mine,) = svc.cut_puzzles(moves, user_color="white")
    assert svc.orientation_of(mine.fen) == "white"

    # A game with no known colour still yields every error, as before.
    assert len(svc.cut_puzzles(moves)) == 1


def test_user_color_of_falls_back_to_the_player_names() -> None:
    game = Game(source="pgn", pgn="*", white="Duke", black="rival", result="*")
    assert svc.user_color_of(game, "duke") == "white"
    assert svc.user_color_of(game, " RIVAL ") == "black"
    assert svc.user_color_of(game, "someone") is None
    assert svc.user_color_of(game, None) is None
    game.user_color = "black"
    assert svc.user_color_of(game, "duke") == "black"  # the stored colour wins


# ---------- verification (pure) ----------


def test_verify_depth_never_drops_below_the_floor() -> None:
    assert svc.verify_depth(8) == svc.VERIFY_DEPTH
    assert svc.verify_depth(None) == svc.VERIFY_DEPTH
    assert svc.verify_depth(24) == 24


def test_verified_drops_what_a_deeper_look_contradicts() -> None:
    error, reply = back_rank_moves()
    puzzle = svc.cut_puzzle(error, reply)
    assert puzzle is not None
    board = chess.Board(puzzle.fen)

    assert svc.verified(puzzle, [reply.lines[0]])  # the engine still says Qe8+

    # The stored first move is not the best move: this is how a knight recapture that walks
    # into mate in two was once served to the user as the answer.
    assert not svc.verified(puzzle, [_line(board, ["Kf1"], Score(cp=30))])
    # The solver is the one getting mated, so there is nothing to punish.
    assert not svc.verified(puzzle, [_line(board, BACK_RANK_MATE, Score(mate=-2))])
    # Nothing the engine can speak to is trusted.
    assert not svc.verified(puzzle, [])
    assert not svc.verified(puzzle, [EngineLine(rank=1, score=Score(cp=0), pv=[], pv_uci=[])])


# ---------- spaced repetition (pure) ----------


def test_sm2_schedule() -> None:
    puzzle = Puzzle(fen=BACK_RANK_BEFORE, ease=2.5, reps=0, interval_days=0.0, lapses=0)
    t0 = datetime(2026, 9, 2, 12, 0, 0)

    svc.sm2_update(puzzle, True, now=t0)
    assert (puzzle.reps, puzzle.interval_days, puzzle.ease) == (1, 1.0, 2.6)
    assert puzzle.due_at == t0 + timedelta(days=1)

    svc.sm2_update(puzzle, True, now=t0)
    assert (puzzle.reps, puzzle.interval_days, puzzle.ease) == (2, 6.0, 2.7)

    svc.sm2_update(puzzle, True, now=t0)
    assert (puzzle.reps, puzzle.interval_days, puzzle.ease) == (3, 16.2, 2.8)  # 6 * 2.7
    assert puzzle.due_at == t0 + timedelta(days=16.2)

    svc.sm2_update(puzzle, False, now=t0)
    assert (puzzle.reps, puzzle.interval_days, puzzle.ease, puzzle.lapses) == (0, 0.0, 2.6, 1)
    assert puzzle.due_at == t0 + timedelta(minutes=10)


def test_sm2_ease_is_bounded() -> None:
    high = Puzzle(fen=BACK_RANK_BEFORE, ease=2.95, reps=5, interval_days=30.0, lapses=0)
    svc.sm2_update(high, True)
    assert high.ease == 3.0
    assert high.interval_days == 88.5  # 30 * 2.95, computed before the ease bump

    low = Puzzle(fen=BACK_RANK_BEFORE, ease=1.4, reps=2, interval_days=6.0, lapses=0)
    svc.sm2_update(low, False)
    assert low.ease == 1.3


# ---------- persistence ----------


async def test_generate_dedupes_the_same_position_per_user() -> None:
    await make_user("rixile", platform="local")
    await make_user("guest", platform="local")
    game_id = await store_game(back_rank_moves())
    async with db.session_factory()() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        first = await svc.generate_from_game(session, game, back_rank_moves(), username="rixile")
        second = await svc.generate_from_game(session, game, back_rank_moves(), username="rixile")
        other = await svc.generate_from_game(session, game, back_rank_moves(), username="guest")
        assert len(first) == 1 and second == [] and len(other) == 1
        users = (await session.execute(select(User).order_by(User.id))).scalars().all()
        assert [(u.username, u.platform) for u in users] == [
            ("rixile", "local"),
            ("guest", "local"),
        ]
        assert first[0].user_id == users[0].id and other[0].user_id == users[1].id
        assert first[0].game_id == game_id
        total = (await session.execute(select(func.count()).select_from(Puzzle))).scalar_one()
        assert total == 2

        due_rixile = await svc.due_puzzles(session, username="rixile")
        assert [p.id for p in due_rixile] == [first[0].id]
        assert len(await svc.due_puzzles(session)) == 2


async def test_one_name_is_one_user_across_training_and_profile() -> None:
    """'Duke' and 'duke' used to be two accounts: the profile counted the puzzles of both while
    the training screen, matching the case exactly, showed none of them."""
    duke_id = await make_user("duke", platform="chesscom")
    game_id = await store_game(back_rank_moves())
    async with db.session_factory()() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        created = await svc.generate_from_game(session, game, back_rank_moves(), username="Duke")
        assert [p.user_id for p in created] == [duke_id]

        # No second account was minted from the query parameter.
        rows = (await session.execute(select(User))).scalars().all()
        assert [(u.username, u.platform) for u in rows] == [("duke", "chesscom")]

        # Training and the profile now count the same deck, whatever case the name arrives in.
        assert [p.id for p in await svc.due_puzzles(session, username="DUKE")] == [created[0].id]
        assert (await svc.training_summary(session, username="duke")).due_puzzles == 1
        found = await profile_svc.find_users(session, "Duke")
        assert (
            await profile_svc.due_puzzle_count(session, [u.id for u in found], svc.now_utc()) == 1
        )

        with pytest.raises(user_svc.UserNotFound):
            await svc.generate_from_game(session, game, back_rank_moves(), username="ghost")
    async with db.session_factory()() as session:
        assert (await session.execute(select(func.count()).select_from(User))).scalar_one() == 1


async def test_generate_skips_the_owners_own_blunder() -> None:
    """Black plays Rxa2 and the deck belongs to Black: nothing to solve as White."""
    game_id = await store_game(back_rank_moves(), user_color="black")
    async with db.session_factory()() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        assert await svc.generate_from_game(session, game, back_rank_moves()) == []


async def test_generate_defaults_to_the_games_owner() -> None:
    async with db.session_factory()() as session:
        session.add(User(username="owner", platform="chesscom"))
        await session.commit()
        owner_id = (await session.execute(select(User.id))).scalar_one()
    game_id = await store_game(qd7_moves(), user_id=owner_id)
    async with db.session_factory()() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        created = await svc.generate_from_game(session, game, qd7_moves())
        assert [p.user_id for p in created] == [owner_id]
        summary = await svc.training_summary(session, username="owner")
        assert summary.due_puzzles == 1
        assert [(m.kind, m.count) for m in summary.motif_sets] == [("discovered_attack", 1)]


# ---------- HTTP ----------


async def test_from_game_flow_and_attempts(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user("me")
    game_id = await store_game(back_rank_moves())
    calls = patch_engine(monkeypatch, stored_lines(back_rank_moves()))

    res = await aclient.post(f"/training/puzzles/from-game/{game_id}", params={"username": "me"})
    assert res.status_code == 200, res.text
    (out,) = res.json()
    # The candidate was re-checked, never below the floor even though the row says depth 16.
    assert calls == [(out["fen"], svc.VERIFY_DEPTH)]
    assert out["orientation"] == "white"
    assert out["solution"] == ["e2e8", "d8e8", "e1e8"]
    assert out["motif"] is None
    assert out["source_game_id"] == game_id and out["source_ply"] == 60
    assert out["reps"] == 0 and out["interval_days"] == 0.0
    assert datetime.fromisoformat(out["due_at"]) <= _now() + timedelta(seconds=1)

    again = await aclient.post(f"/training/puzzles/from-game/{game_id}", params={"username": "me"})
    assert again.status_code == 200 and again.json() == []

    due = await aclient.get("/training/puzzles/due", params={"username": "me", "limit": 5})
    assert [p["id"] for p in due.json()] == [out["id"]]
    nobody = await aclient.get("/training/puzzles/due", params={"username": "someone-else"})
    assert nobody.json() == []

    one = await aclient.get(f"/training/puzzles/{out['id']}")
    assert one.status_code == 200 and one.json()["fen"] == out["fen"]

    summary = await aclient.get("/training/summary", params={"username": "me"})
    assert summary.json() == {
        "due_puzzles": 1,
        "motif_sets": [],
        "studies": [],
    }

    before = _now()
    solved = await aclient.post(
        f"/training/puzzles/{out['id']}/attempt", json={"correct": True, "seconds": 12.5}
    )
    assert solved.status_code == 200, solved.text
    body = solved.json()
    assert body["reps"] == 1 and body["interval_days"] == 1.0
    due_at = datetime.fromisoformat(body["due_at"])
    assert timedelta(days=1) <= due_at - before <= timedelta(days=1, seconds=5)

    assert (await aclient.get("/training/puzzles/due", params={"username": "me"})).json() == []
    assert (await aclient.get("/training/summary")).json()["due_puzzles"] == 0

    before = _now()
    failed = await aclient.post(
        f"/training/puzzles/{out['id']}/attempt", json={"correct": False, "seconds": 40}
    )
    body = failed.json()
    assert body["reps"] == 0 and body["interval_days"] == 0.0
    due_at = datetime.fromisoformat(body["due_at"])
    assert timedelta(minutes=10) <= due_at - before <= timedelta(minutes=10, seconds=5)

    async with db.session_factory()() as session:
        attempts = (
            (await session.execute(select(PuzzleAttempt).order_by(PuzzleAttempt.id)))
            .scalars()
            .all()
        )
        assert [(a.correct, a.seconds) for a in attempts] == [(True, 12.5), (False, 40.0)]
        puzzle = await session.get(Puzzle, out["id"])
        assert puzzle is not None and puzzle.lapses == 1 and puzzle.ease == 2.4


async def test_summary_counts_motifs_across_games(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user("me")
    patch_engine(monkeypatch, stored_lines(back_rank_moves(), qd7_moves()))
    for moves in (back_rank_moves(), qd7_moves()):
        game_id = await store_game(moves)
        res = await aclient.post(
            f"/training/puzzles/from-game/{game_id}", params={"username": "me"}
        )
        assert res.status_code == 200 and len(res.json()) == 1
    summary = (await aclient.get("/training/summary", params={"username": "me"})).json()
    assert summary["due_puzzles"] == 2
    # only the Qd7 puzzle carries a motif; the back-rank sacrifice is tagged with none
    assert summary["motif_sets"] == [{"kind": "discovered_attack", "count": 1}]


async def test_from_game_requires_a_completed_analysis(aclient: AsyncClient) -> None:
    missing = await aclient.post("/training/puzzles/from-game/999")
    assert missing.status_code == 404

    running = await store_game(back_rank_moves(), status="running")
    res = await aclient.post(f"/training/puzzles/from-game/{running}")
    assert res.status_code == 409
    assert "분석" in res.json()["detail"]

    async with db.session_factory()() as session:
        session.add(Game(source="pgn", source_id="bare", pgn="*", white="w", black="b"))
        await session.commit()
        bare_id = (
            await session.execute(select(Game.id).where(Game.source_id == "bare"))
        ).scalar_one()
    assert (await aclient.post(f"/training/puzzles/from-game/{bare_id}")).status_code == 409


async def test_from_game_rejects_an_unknown_username(aclient: AsyncClient) -> None:
    """A query parameter no longer mints an account whose deck the profile would count and the
    training screen would never show."""
    game_id = await store_game(back_rank_moves())
    res = await aclient.post(f"/training/puzzles/from-game/{game_id}", params={"username": "ghost"})
    assert res.status_code == 404
    assert "사용자" in res.json()["detail"]
    async with db.session_factory()() as session:
        assert (await session.execute(select(func.count()).select_from(User))).scalar_one() == 0


async def test_from_game_drops_a_puzzle_the_engine_contradicts(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored line came from whatever depth the game was analysed at. When a deeper look
    names a different best move, the candidate is dropped instead of taught."""
    await make_user("me")
    game_id = await store_game(back_rank_moves())
    fen = back_rank_moves()[0].fen_after
    patch_engine(monkeypatch, {fen: [_line(chess.Board(fen), ["Kf1"], Score(cp=30))]})

    res = await aclient.post(f"/training/puzzles/from-game/{game_id}", params={"username": "me"})
    assert res.status_code == 200, res.text
    assert res.json() == []
    async with db.session_factory()() as session:
        assert (await session.execute(select(func.count()).select_from(Puzzle))).scalar_one() == 0


async def test_from_game_reports_a_missing_engine(
    aclient: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_user("me")
    game_id = await store_game(back_rank_moves())

    async def no_engine(*_args: object, **_kwargs: object) -> list[EngineLine]:
        raise RuntimeError("Stockfish not found")

    monkeypatch.setattr(analysis_svc, "get_lines", no_engine)
    res = await aclient.post(f"/training/puzzles/from-game/{game_id}", params={"username": "me"})
    assert res.status_code == 503 and "엔진" in res.json()["detail"]


async def test_unknown_puzzle_is_404(aclient: AsyncClient) -> None:
    assert (await aclient.get("/training/puzzles/123")).status_code == 404
    res = await aclient.post("/training/puzzles/123/attempt", json={"correct": True})
    assert res.status_code == 404
    assert (await aclient.get("/training/_status")).json()["module"] == "training"
