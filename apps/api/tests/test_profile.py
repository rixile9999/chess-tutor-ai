"""Weakness report (services/profile.py) and GET /profile/{username}.

The analyses are fabricated with the pydantic models from schemas.py, but every move, FEN and
best move is replayed with python-chess, and the tactical claims the report is built on (a
knight fork on c2, a queen pin from d3, a knight fork from b3) are real motifs in real games.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import chess
import chess.pgn
from httpx import AsyncClient

from chess_tutor import db, openings
from chess_tutor.models import Analysis, Game, Puzzle, User
from chess_tutor.schemas import EngineLine, MoveAnalysis, Score
from chess_tutor.services import profile as svc
from chess_tutor.services.analysis import classify_loss, win_prob_loss

USERNAME = "tester"

# Game A, user White, lost: Fried Liver, then 9.a3?? (best 9.Qd3, which guards c2 and pins
# Nd5 to Qd8) 9...Nxc2+ forks. Bc4 already pins Nd5 to Ke6, so 9.Bb3 would keep that pin, not
# make one, and the missed-motif counter would see nothing.
FRIED_LIVER = (
    "e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Nxd5 Nxf7 Kxf7 Qf3+ Ke6 Nc3 Ncb4 a3 Nxc2+ Kd1 Nxa1"
).split()
# Game B, user Black, lost: French Advance; 8...Bd7? instead of 8...Nb3 forking a1 and d2.
FRENCH = (
    "e4 e6 d4 d5 e5 c5 c3 Nc6 Nf3 Qb6 a3 c4 Nbd2 Na5 Be2 Bd7 O-O Ne7 Re1 Nf5 Nf1 h5 "
    "Bg5 Be7 Bxe7 Kxe7 Ng3 Nxg3 hxg3 Rag8 Qd2 f5"
).split()
# Game C, user White, won: a rook ending from a FEN, every move best.
ROOK_ENDING_FEN = "8/5pk1/6p1/8/8/4R3/r4PPP/6K1 w - - 0 40"
ROOK_ENDING = "Re7 Kf6 Rb7 Ra5 Kf1 g5 Ke2 Kg6 Rb6+ Kf5".split()
# Unanalysed games for the repertoire: a Ruy Lopez that stays in the book, a Fried Liver twin.
RUY_LOPEZ = "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O h3 Na5 Bc2 c5 d4 Qc7".split()
FRIED_LIVER_LONGER = [*FRIED_LIVER, "Nxd5"]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def fabricate(
    sans: list[str],
    evals: list[int],
    *,
    start_fen: str | None = None,
    best: dict[int, str] | None = None,
    clocks: dict[int, float] | None = None,
) -> list[MoveAnalysis]:
    """Analysis of a game whose evaluation after each ply is ``evals[ply]`` (White's view,
    ``evals[0]`` is the start). ``best`` overrides the engine's best move per ply (default:
    the move played, which then loses nothing), ``clocks`` the seconds left (default 90)."""
    assert len(evals) == len(sans) + 1
    board = chess.Board(start_fen) if start_fen else chess.Board()
    replay = board.copy()
    moves = []
    for san in sans:
        move = replay.parse_san(san)
        moves.append(move)
        replay.push(move)
    _, left_at = openings.classify_game(moves, start=board.copy())

    out: list[MoveAnalysis] = []
    for i, san in enumerate(sans):
        ply = i + 1
        move = board.parse_san(san)
        best_san = (best or {}).get(ply, san)
        best_move = board.parse_san(best_san)
        color = "white" if board.turn == chess.WHITE else "black"
        fen_before = board.fen()
        before, after = Score(cp=evals[i]), Score(cp=evals[i + 1])
        loss = 0.0 if move == best_move else win_prob_loss(before, after, color)
        board.push(move)
        book = i < left_at and openings.lookup(board) is not None
        out.append(
            MoveAnalysis(
                ply=ply,
                san=san,
                uci=move.uci(),
                color=color,
                fen_before=fen_before,
                fen_after=board.fen(),
                eval_before=before,
                eval_after=after,
                best_move_san=best_san,
                best_move_uci=best_move.uci(),
                classification="book" if book else classify_loss(loss),
                win_prob_loss=round(loss, 6),
                lines=[EngineLine(rank=1, score=before, pv=[best_san], pv_uci=[best_move.uci()])],
                clock=(clocks or {}).get(ply, 90.0),
            )
        )
    return out


def pgn_text(sans: list[str], result: str, tag: str, start_fen: str | None = None) -> str:
    game = chess.pgn.Game()
    if start_fen:
        game.setup(chess.Board(start_fen))
    game.headers["Event"] = tag
    game.headers["White"] = USERNAME if tag.endswith("w") else "opp"
    game.headers["Black"] = "opp" if tag.endswith("w") else USERNAME
    game.headers["Result"] = result
    node: chess.pgn.GameNode = game
    for san in sans:
        node = node.add_variation(node.board().parse_san(san))
    return str(game)


def game_a() -> list[MoveAnalysis]:
    evals = [30] * 17 + [-500, -500, -500, -900]
    return fabricate(FRIED_LIVER, evals, best={17: "Qd3"}, clocks={17: 12.0})


def game_b() -> list[MoveAnalysis]:
    evals = [20] * 15 + [25] + [150] * 17
    return fabricate(FRENCH, evals, best={16: "Nb3"}, clocks={16: 20.0, 30: 25.0})


def game_c() -> list[MoveAnalysis]:
    return fabricate(ROOK_ENDING, [0] * 11, start_fen=ROOK_ENDING_FEN)


async def seed(now: datetime) -> int:
    """User 'tester' (rapid 1500) with three analysed games two days ago, two unanalysed
    games, one analysed game outside a 60-day window, and two puzzles (one due)."""
    async with db.session_factory()() as session:
        user = User(username=USERNAME, platform="chesscom", rating_rapid=1500)
        session.add(user)
        await session.flush()
        recent = now - timedelta(days=2)
        specs: list[
            tuple[str, list[str], str, str, datetime, list[MoveAnalysis] | None, str | None]
        ] = [
            ("a-w", FRIED_LIVER, "0-1", "white", recent, game_a(), None),
            ("b-b", FRENCH, "1-0", "black", recent, game_b(), None),
            ("c-w", ROOK_ENDING, "1-0", "white", recent, game_c(), ROOK_ENDING_FEN),
            ("d-w", RUY_LOPEZ, "1-0", "white", recent, None, None),
            ("e-w", FRIED_LIVER_LONGER, "1/2-1/2", "white", recent, None, None),
            ("old-w", FRIED_LIVER, "0-1", "white", now - timedelta(days=100), game_a(), None),
        ]
        for tag, sans, result, color, played_at, moves, fen in specs:
            game = Game(
                user_id=user.id,
                source="pgn",
                source_id=tag,
                pgn=pgn_text(sans, result, tag, fen),
                white=USERNAME if color == "white" else "opp",
                black="opp" if color == "white" else USERNAME,
                result=result,
                played_at=played_at,
                user_color=color,
                ply_count=len(sans),
            )
            session.add(game)
            await session.flush()
            if moves is not None:
                session.add(
                    Analysis(
                        game_id=game.id,
                        status="done",
                        summary={},
                        moves=[m.model_dump(mode="json") for m in moves],
                    )
                )
        session.add_all(
            [
                Puzzle(user_id=user.id, fen=ROOK_ENDING_FEN, due_at=now - timedelta(days=1)),
                Puzzle(user_id=user.id, fen=ROOK_ENDING_FEN, due_at=now + timedelta(days=1)),
            ]
        )
        await session.commit()
        return user.id


# ---------- the fabricated games are real chess ----------


def test_fabricated_games_hold_the_claimed_facts() -> None:
    a, b = game_a(), game_b()
    blunder = a[16]
    assert (blunder.san, blunder.classification, blunder.color) == ("a3", "blunder", "white")
    assert blunder.best_move_san == "Qd3" and blunder.clock == 12.0
    assert svc.motif_kinds(blunder.fen_before, blunder.best_move_uci) == {"pin"}
    reply = a[17]
    assert reply.san == "Nxc2+" and reply.lines[0].pv_uci == [reply.uci]
    assert svc.motif_kinds(reply.fen_before, reply.uci) == {"fork"}
    assert a[0].classification == "book" and a[10].classification == "book"

    mistake = b[15]
    assert (mistake.san, mistake.classification, mistake.color) == ("Bd7", "mistake", "black")
    assert svc.motif_kinds(mistake.fen_before, mistake.best_move_uci) == {"fork"}
    assert svc.structure_key_of(b) == "french_chain"
    assert svc.structure_key_of(a) == "unclassified"
    assert svc.structure_key_of(game_c()) == "open_center"


# ---------- pure helpers ----------


def test_phase_by_material_then_book_window() -> None:
    a, c = game_a(), game_c()
    until = svc.opening_until(a)
    assert until == 17  # last book ply 11 (6.Nxf7) + 6
    assert svc.phase_of(a[16], until) == "opening"
    assert svc.phase_of(a[18], until) == "middlegame"
    assert svc.opening_until(c) == 20  # no book at all
    assert svc.non_pawn_material(chess.Board(ROOK_ENDING_FEN)) == 10
    assert all(svc.phase_of(m, 20) == "endgame" for m in c)


def test_cp_loss_is_capped_and_mate_aware() -> None:
    a = game_a()
    assert svc.cp_loss(a[16]) == 500  # +30 -> -500 is 530, capped
    assert svc.cp_loss(a[0]) == 0
    mate = a[16].model_copy(update={"eval_after": Score(mate=-3)})
    assert svc.cp_loss(mate) == 500
    gain = a[16].model_copy(update={"eval_after": Score(cp=200)})
    assert svc.cp_loss(gain) == 0


def test_repertoire_labels_and_deviation() -> None:
    moves = [chess.Move.from_uci(u) for u in ("e2e4", "c7c5")]
    assert svc.repertoire_label("white", moves) == "1.e4 c5"
    assert svc.repertoire_label("black", moves) == "1.e4 상대"
    assert svc.repertoire_label("white", moves[:1]) is None
    assert svc.repertoire_label("black", []) is None

    def replay(sans: list[str]) -> list[chess.Move]:
        b = chess.Board()
        out = []
        for s in sans:
            out.append(b.push_san(s))
        return out

    assert svc.left_book_early(replay(FRIED_LIVER))  # book ends at move 6
    assert not svc.left_book_early(replay(RUY_LOPEZ))  # never leaves the book
    assert svc.left_book_early(replay("a3 a6 h3 h6 b3 b6 g3 g6".split()))  # no opening at all


def test_korean_particle_and_rating_band() -> None:
    assert svc._eul("포크") == "포크를"
    assert svc._eul("디스커버드 어택") == "디스커버드 어택을"
    assert svc._eul("IQP") == "IQP를"
    assert svc._ro("프렌치 사슬") == "프렌치 사슬로"  # ㄹ takes 로
    assert svc._ro("헤지호그") == "헤지호그로"
    assert svc._ro("킹스 인디언") == "킹스 인디언으로"
    assert svc.rating_band(1500) == ("1400-1600", (82.0, 77.0, 66.0))
    assert svc.rating_band(2300)[0] == "2000+"
    assert svc.rating_band(900)[0] == "0-1200"


# ---------- the report ----------


async def test_report_over_three_analysed_games() -> None:
    now = _now()
    await seed(now)
    async with db.session_factory()() as session:
        report = await svc.build_report(session, "TESTER", days=60)

    assert report.username == USERNAME and report.platform == "chesscom"
    assert report.rating_rapid == 1500 and report.rating_blitz is None
    assert report.games == 5 and report.analyzed_games == 3
    assert report.window_from is not None and report.window_to is not None
    assert timedelta(days=60) - timedelta(seconds=5) <= report.window_to - report.window_from

    phases = report.phase_accuracy
    assert phases is not None
    assert (phases.opening_moves, phases.middlegame_moves, phases.endgame_moves) == (17, 9, 5)
    assert 85.0 < phases.opening < 95.0  # 15 best moves, one blunder, one mistake
    assert phases.middlegame == 100.0 and phases.endgame == 100.0
    assert phases.baseline_band == "1400-1600"
    assert phases.delta_opening == round(phases.opening - 82.0, 1)
    assert phases.delta_middlegame == 23.0 and phases.delta_endgame == 34.0

    assert [(s.key, s.games, s.win_rate, s.wins, s.losses) for s in report.structures] == [
        ("french_chain", 1, 0.0, 0, 1),
        ("open_center", 1, 1.0, 1, 0),
    ]
    french, rook = report.structures
    assert french.name == "프렌치 사슬" and french.avg_loss_cp == 7.8  # 125 cp over 16 moves
    assert (french.break_label, french.avg_break_move) == ("…f5", 16.0)
    assert rook.avg_loss_cp == 0.0 and rook.break_label is None

    assert [(m.kind, m.count) for m in report.motifs_missed] == [("fork", 2), ("pin", 1)]

    time = report.time
    assert time is not None
    assert (time.moves_under_30s, time.moves_over_30s) == (3, 28)
    assert time.blunder_rate_under_30s == 0.667 and time.blunder_rate_over_30s == 0.0
    assert time.baseline == 0.09

    training = report.training
    assert training is not None
    assert training.due_puzzles == 1
    assert [(m.kind, m.count) for m in training.motif_sets] == [("fork", 2), ("pin", 1)]
    assert training.studies == ["프렌치 사슬 …f5 타이밍", "오픈 센터 계획"]

    assert len(report.repertoire_holes) == 1
    hole = report.repertoire_holes[0]
    assert hole.label == "1.e4 e5" and hole.games == 3
    assert hole.deviation_rate == 0.667 and hole.win_rate == 0.333
    assert hole.avg_loss_cp == 50.0  # one capped 500 cp blunder over 10 White moves

    text = report.summary_text
    assert "프렌치 사슬" in text and "1판 중 0승" in text
    assert "…f5 브레이크는 평균 16수" in text
    assert "포크를 2번 놓쳤습니다" in text
    assert "30초 미만에 둔 수 3개의 블런더율은 67%로" in text
    assert "—" not in text
    assert 2 <= text.count("습니다") <= 3


async def test_empty_window_returns_zeros() -> None:
    now = _now()
    await seed(now)
    async with db.session_factory()() as session:
        session.add(User(username="lonely", platform="lichess"))
        await session.commit()
        lonely = await svc.build_report(session, "lonely")
        narrow = await svc.build_report(session, USERNAME, days=1)

    for report in (lonely, narrow):
        assert report.games == 0 and report.analyzed_games == 0
        assert report.phase_accuracy is None and report.time is None
        assert report.structures == [] and report.motifs_missed == []
        assert report.repertoire_holes == []
        assert report.training is not None and report.training.motif_sets == []
        assert report.training.studies == []
        assert "게임이 없습니다" in report.summary_text
    assert lonely.platform == "lichess" and lonely.training is not None
    assert lonely.training.due_puzzles == 0
    assert narrow.training is not None and narrow.training.due_puzzles == 1


async def test_games_without_analysis_only_count() -> None:
    async with db.session_factory()() as session:
        user = User(username="fresh", platform="local")
        session.add(user)
        await session.flush()
        session.add(
            Game(
                user_id=user.id,
                source="pgn",
                source_id="fresh-1",
                pgn=pgn_text(RUY_LOPEZ, "1-0", "fresh-w"),
                white="fresh",
                black="opp",
                result="1-0",
                user_color="white",
            )
        )
        await session.commit()
        report = await svc.build_report(session, "fresh")
    assert report.games == 1 and report.analyzed_games == 0
    assert report.phase_accuracy is None and report.structures == []
    assert "1판을 가져왔지만 분석이 끝난 게임이 없습니다" in report.summary_text


# ---------- HTTP ----------


async def test_get_profile(aclient: AsyncClient) -> None:
    await seed(_now())
    res = await aclient.get(f"/profile/{USERNAME}", params={"days": 60})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["username"] == USERNAME and body["games"] == 5
    assert body["analyzed_games"] == 3
    assert body["motifs_missed"] == [{"kind": "fork", "count": 2}, {"kind": "pin", "count": 1}]
    assert body["training"]["due_puzzles"] == 1
    assert body["repertoire_holes"][0]["label"] == "1.e4 e5"
    assert body["phase_accuracy"]["endgame"] == 100.0
    assert datetime.fromisoformat(body["window_to"]) >= datetime.fromisoformat(body["window_from"])

    default_days = await aclient.get(f"/profile/{USERNAME}")
    assert default_days.status_code == 200 and default_days.json()["games"] == 5

    narrow = await aclient.get(f"/profile/{USERNAME}", params={"days": 1})
    assert narrow.status_code == 200 and narrow.json()["games"] == 0

    assert (await aclient.get(f"/profile/{USERNAME}", params={"days": 0})).status_code == 422
    missing = await aclient.get("/profile/nobody")
    assert missing.status_code == 404 and "사용자" in missing.json()["detail"]
    assert (await aclient.get("/profile/_status")).json() == {"module": "profile", "status": "ok"}
