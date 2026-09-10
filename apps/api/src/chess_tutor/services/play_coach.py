"""Coach for a live position: three hint levels and the after-move blunder check.

  hint(req) -> PlayHintResponse
    level 1: structure.classify + plans.match_plans (played moves from start_fen/moves_san);
             no move is named; text lists the structure and the side's plans
    level 2: level 1 + maia.move_probs top 2-3 as candidates with reasoning.explain_alternative
    level 3: level 2 + analysis.get_lines best line, motifs.detect, computer_move flag
    Every sentence's claims go through verify.verify_all; text keeps only verified sentences
    (verified/verified_claims/total_claims report the tally).
  check(req) -> PlayCheckResponse
    shallow (depth default 12) win-probability loss of `san` from fen_before, classified with
    analysis.classify_loss; best move from the cached lines; reason via explain_alternative;
    when the best move is a computer move at this rating, alternative_san is the most probable
    natural move whose loss is below the mistake threshold.
  Both raise ValueError for a bad FEN/SAN.

Both entry points are sync and run in FastAPI's thread pool. The engine is reached through
`engine.pool.borrow()` rather than the async, DB-cached `analysis.get_lines`: one borrow serves
the whole request, and a hint that is only ever asked for once gains nothing from the cache.
"""

from __future__ import annotations

import chess

from chess_tutor import structure
from chess_tutor.config import get_settings
from chess_tutor.engine import pool
from chess_tutor.motifs import detect
from chess_tutor.schemas import (
    Color,
    EngineLine,
    HintBest,
    HintCandidate,
    MotifOut,
    Plan,
    PlayCheckRequest,
    PlayCheckResponse,
    PlayHintRequest,
    PlayHintResponse,
    Score,
)
from chess_tutor.services import analysis, maia
from chess_tutor.services import plans as kb
from chess_tutor.services.reasoning import explain_alternative
from chess_tutor.verify import Claim, verify_all

MULTIPV = 3
"""Lines asked of the engine: enough for the played move to be among them fairly often."""
CANDIDATES = 3
"""Natural moves shown at hint level 2."""
MISTAKE_LOSS = analysis._MISTAKE
"""Win-probability loss at which a move becomes a mistake. Taken from the review pipeline so a
move this module offers as a natural alternative is never one /play/check would then flag."""

COLOR_KO: dict[Color, str] = {"white": "백", "black": "흑"}

NO_MOVES = "이미 끝난 국면입니다."
ILLEGAL_POSITION = "체스 규칙에 맞지 않는 국면입니다."
"""Stockfish dies on a position the rules forbid (a side to move with the opponent already in
check, two kings of one colour), which would take a pooled engine down with it, so such a FEN
is refused before the engine sees it - the same guard routers/analysis.py puts on /position."""


class _Sentence:
    """A piece of the Korean answer together with the board facts it states."""

    def __init__(self, text: str, claims: list[Claim] | None = None) -> None:
        self.text = text
        self.claims = list(claims or [])


def _side(board: chess.Board) -> Color:
    return "white" if board.turn == chess.WHITE else "black"


def _board(fen: str) -> chess.Board:
    """The position, refused when it breaks the rules. Raises ValueError either way."""
    board = chess.Board(fen)
    if not board.is_valid():
        raise ValueError(ILLEGAL_POSITION)
    return board


def _percent(prob: float) -> str:
    """A move played once in five hundred games should not read as '0%'."""
    pct = prob * 100
    return f"{pct:.1f}%" if 0.0 < pct < 10.0 else f"{pct:.0f}%"


def _depth(requested: int | None) -> int:
    return requested or get_settings().play_depth


def _pv_moves(board: chess.Board, line: EngineLine) -> list[chess.Move]:
    """The line's moves, stopping at the first one that does not fit the board."""
    moves: list[chess.Move] = []
    probe = board.copy()
    for uci in line.pv_uci:
        move = chess.Move.from_uci(uci)
        if not probe.is_legal(move):
            break
        moves.append(move)
        probe.push(move)
    return moves


def _played_moves(
    start_fen: str | None, moves_san: list[str]
) -> tuple[chess.Board, list[chess.Move]]:
    """The game so far, so plans can be marked executed. Raises ValueError on an illegal move."""
    start = chess.Board(start_fen) if start_fen else chess.Board()
    board = start.copy()
    moves: list[chess.Move] = []
    for san in moves_san:
        move = board.parse_san(san)
        moves.append(move)
        board.push(move)
    return start, moves


def _assemble(sentences: list[_Sentence]) -> tuple[str, bool, int, int]:
    """(text, verified, verified_claims, total_claims). A sentence whose claims do not all hold
    is dropped: principle 1 says the user only reads statements the verifier confirmed."""
    kept: list[str] = []
    verified_claims = 0
    total_claims = 0
    for sentence in sentences:
        verdicts = verify_all(sentence.claims)
        holds = [v for v in verdicts if v.holds]
        total_claims += len(verdicts)
        verified_claims += len(holds)
        if len(holds) == len(verdicts):
            kept.append(sentence.text)
    return " ".join(kept), verified_claims == total_claims, verified_claims, total_claims


# ---------- hint ----------


def _plan_sentences(board: chess.Board, side: Color, plans: list[Plan]) -> list[_Sentence]:
    """Level 1: the structure and what this side usually does in it. Names no concrete move
    for this position - only plan titles, which are the knowledge base's own wording."""
    todo = [p.title for p in plans if p.status in ("pv_match", "later")][:3]
    done = [p.title for p in plans if p.status == "executed"][:2]
    out: list[_Sentence] = []
    who = COLOR_KO[side]
    if todo:
        out.append(_Sentence(f"{who}의 전형적 계획은 {', '.join(todo)}입니다."))
    if done:
        out.append(_Sentence(f"이미 실행한 계획: {', '.join(done)}."))
    if not todo and not done:
        out.append(
            _Sentence("이 구조에는 등록된 계획이 없습니다. 전개와 킹 안전을 먼저 살펴보세요.")
        )
    return out


def _candidates(
    board: chess.Board,
    probs: dict[str, float],
    lines: list[EngineLine],
) -> list[HintCandidate]:
    """Level 2: the moves a player of this rating actually plays here, with a reason each."""
    out: list[HintCandidate] = []
    pov = _side(board)
    for san, prob in list(probs.items())[:CANDIDATES]:
        move = board.parse_san(san)
        line = analysis.played_line(lines, move.uci())
        pv = _pv_moves(board, line) if line is not None else [move]
        reason, claims = explain_alternative(board, san, pv, Score(), Score(), pov)
        out.append(
            HintCandidate(
                san=board.san(move),
                uci=move.uci(),
                prob=round(prob, maia.PRECISION),
                reason=reason,
                claims=claims,
            )
        )
    return out


def _best(board: chess.Board, lines: list[EngineLine], probs: dict[str, float]) -> HintBest | None:
    """Level 3: the engine's move, why it works, and whether a human would find it."""
    if not lines or not lines[0].pv:
        return None
    line = lines[0]
    san = line.pv[0]
    move = board.parse_san(san)
    reason, claims = explain_alternative(
        board, san, _pv_moves(board, line), line.score, line.score, _side(board)
    )
    prob = probs.get(board.san(move))
    return HintBest(
        san=board.san(move),
        uci=move.uci(),
        pv=list(line.pv),
        score=line.score,
        reason=reason,
        motifs=[MotifOut.model_validate(m.as_dict()) for m in detect(board, move)],
        claims=claims,
        computer_move=prob is not None and prob < maia.COMPUTER_MOVE_THRESHOLD,
    )


def hint(req: PlayHintRequest) -> PlayHintResponse:
    """What to say at this hint level, with every sentence verified."""
    board = _board(req.fen)
    if not any(board.legal_moves):
        raise ValueError(NO_MOVES)
    side = _side(board)
    info = structure.classify(board)
    start_board, played = _played_moves(req.start_fen, req.moves_san)

    lines: list[EngineLine] = []
    if req.level >= 3:
        with pool.borrow() as borrowed:
            lines = analysis.analyse_position(board.fen(), _depth(req.depth), MULTIPV, borrowed)
    pvs = [moves for line in lines if (moves := _pv_moves(board, line))]

    plans = kb.match_plans(
        info.key,
        side,
        pvs,
        board,
        played,
        mirror=kb.mirrored(info.key, board),
        start_board=start_board,
    )

    sentences = [_Sentence(f"지금 구조는 {info.name}입니다."), *_plan_sentences(board, side, plans)]

    probs: dict[str, float] = {}
    source: maia.Source | None = None
    candidates: list[HintCandidate] = []
    if req.level >= 2:
        probs, source = maia.move_probs(req.fen, req.rating)
        candidates = _candidates(board, probs, lines)
        if candidates:
            listed = ", ".join(f"{c.san}({_percent(c.prob or 0.0)})" for c in candidates)
            sentences.append(
                _Sentence(
                    f"이 레이팅대({req.rating})에서는 {listed}를 둡니다.",
                    [Claim(kind="legal_move", fen=board.fen(), object=c.san) for c in candidates],
                )
            )
            sentences.extend(_Sentence(f"{c.san}: {c.reason}", c.claims) for c in candidates)

    best = _best(board, lines, probs) if req.level >= 3 else None
    if best is not None:
        sentences.append(
            _Sentence(
                f"엔진의 최선수는 {best.san}입니다. {best.reason}",
                [Claim(kind="legal_move", fen=board.fen(), object=best.san), *best.claims],
            )
        )
        if best.computer_move:
            sentences.append(_Sentence("이 레이팅대에서 사람이 거의 두지 않는, 엔진다운 수입니다."))

    text, verified, verified_claims, total_claims = _assemble(sentences)
    return PlayHintResponse(
        level=req.level,
        side=side,
        structure=info,
        plans=plans,
        candidates=candidates,
        best=best,
        text=text,
        source=source,
        verified=verified,
        verified_claims=verified_claims,
        total_claims=total_claims,
    )


# ---------- check ----------


def _natural_alternative(
    board: chess.Board,
    probs: dict[str, float],
    lines: list[EngineLine],
    best_san: str,
) -> tuple[str | None, str]:
    """The most probable move at this rating that is not a mistake, for when the engine's move
    is one only a computer finds. ``probs`` is walked in order, which every backend returns
    highest first. Only moves the engine already scored are considered, so this costs no extra
    search."""
    pov = _side(board)
    eval_before = lines[0].score if lines else Score()
    for san, _prob in probs.items():
        if san == best_san:
            continue
        move = board.parse_san(san)
        line = analysis.played_line(lines, move.uci())
        if line is None:
            continue
        if analysis.win_prob_loss(eval_before, line.score, pov) >= MISTAKE_LOSS:
            continue
        reason, claims = explain_alternative(
            board, san, _pv_moves(board, line), line.score, eval_before, pov
        )
        if not all(v.holds for v in verify_all(claims)):
            reason = ""
        return board.san(move), reason
    return None, ""


def check(req: PlayCheckRequest) -> PlayCheckResponse:
    """Classify the move that was just played, shallow enough to answer mid-game."""
    board = _board(req.fen_before)
    move = board.parse_san(req.san)
    san = board.san(move)
    pov = _side(board)
    depth = _depth(req.depth)

    with pool.borrow() as borrowed:
        lines = analysis.analyse_board(board, depth, MULTIPV, borrowed)
        line = analysis.played_line(lines, move.uci())
        if line is not None:
            eval_after = line.score
        else:
            after = board.copy()
            after.push(move)
            terminal = analysis.terminal_score(after)
            eval_after = (
                terminal
                if terminal is not None
                else analysis.analyse_board(after, depth, 1, borrowed)[0].score
            )

    eval_before = lines[0].score
    loss = analysis.win_prob_loss(eval_before, eval_after, pov)
    classification = "forced" if board.legal_moves.count() == 1 else analysis.classify_loss(loss)
    best_line = lines[0]
    best_san = best_line.pv[0] if best_line.pv else san
    best_move = board.parse_san(best_san)

    reason, claims = explain_alternative(
        board,
        san,
        _pv_moves(board, line) if line is not None else [move],
        eval_after,
        eval_before,
        pov,
    )
    verified = all(v.holds for v in verify_all(claims))
    if not verified:
        reason = ""

    probs, _source = maia.move_probs(req.fen_before, req.rating, include=[best_san])
    best_prob = probs.get(board.san(best_move))
    computer_move = best_prob is not None and best_prob < maia.COMPUTER_MOVE_THRESHOLD
    alternative_san: str | None = None
    alternative_reason = ""
    if computer_move:
        alternative_san, alternative_reason = _natural_alternative(
            board, probs, lines, board.san(best_move)
        )

    return PlayCheckResponse(
        san=san,
        uci=move.uci(),
        classification=classification,
        win_loss=round(loss, 6),
        eval_before=eval_before,
        eval_after=eval_after,
        best_san=board.san(best_move),
        best_uci=best_move.uci(),
        pv=list(best_line.pv),
        reason=reason,
        claims=claims,
        verified=verified,
        computer_move=computer_move,
        alternative_san=alternative_san,
        alternative_reason=alternative_reason,
    )
