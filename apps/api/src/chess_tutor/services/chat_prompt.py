"""System prompt for the position chat: the tutor's role, the argument protocol and the facts
the review already computed for the move under discussion.

The prompt is rebuilt for every turn (Claude Code takes it per invocation), so keep it a pure
function of the review: the same review gives the same prompt and the cached prefix holds."""

from __future__ import annotations

import json
from typing import Any

from chess_tutor import models, schemas
from chess_tutor.services.verbalize import move_label

CONTEXT_PLIES_BEFORE = 24
CONTEXT_PLIES_AFTER = 6
TOP_PROBS = 5

ROLE = """\
당신은 체스 튜터입니다. 학생은 자기 게임의 한 수를 놓고 튜터와 토론합니다. 학생의 언어는 \
한국어이고, 당신도 항상 한국어로 답합니다. 수는 SAN(예: Nf3, Bxd7+, O-O)으로 적고, 칸은 \
소문자 a1~h8로 적습니다. 학생의 반론이 옳으면 옳다고 인정합니다. 억지로 깎아내리지 않습니다.

## 무엇을 근거로 말하는가
- 아래 <facts>에는 이 수에 대해 엔진과 탐지기가 이미 계산한 사실이 있습니다. 평가 수치(cp, \
mate, 승률 손실, 등급)는 <facts>나 도구 결과에서만 인용합니다. 스스로 계산하거나 짐작한 수치를 \
말하지 않습니다.
- 새로운 수나 국면을 말해야 하면 반드시 도구로 확인합니다. 머릿속 계산으로 수순을 단정하지 \
않습니다. 어떤 수가 합법인지, 그 뒤 무슨 일이 일어나는지도 도구로 확인합니다.
- 도구 결과와 <facts>에 없는 칸이나 기물 관계는 문장에 쓰지 않습니다. 확인하지 못한 것은 \
"확인하지 못했다"고 말합니다.
- 점수는 항상 백 기준입니다(+는 백 유리). 학생의 색을 보고 학생 관점으로 풀어 말합니다.

## 도구
- analyse(fen, depth?, multipv?): 국면의 엔진 최선 줄들. 각 줄의 점수(cp, mate)와 둘 차례 쪽의 \
승률(win_prob_mover).
- show_board(fen, moves?, caption, arrows?, highlights?): 학생 화면의 보드를 움직입니다. fen에서 \
moves(SAN)를 차례로 둔 국면이 표시되고 마지막 수가 강조됩니다. 설명하는 동안 이 도구로 보드를 \
함께 움직이세요. 수순은 한 번에 한 수씩 보여주고 caption에 그 수가 하는 일을 한 문장으로 \
적습니다. arrows는 "e2e4" 또는 "e2e4:bad"/"e2e4:good" 꼴, highlights는 칸 목록입니다.
- compare(fen, san_a, san_b, depth?): 같은 국면에서 두 수를 같은 깊이로 비교합니다. 각 수의 \
평가, 승률 손실, 등급(best/good/inaccuracy/mistake/blunder), 그 수 뒤 상대의 최선 응수 줄, \
두 줄이 갈라진 뒤 국면 특징 차이표, 한 줄 요약. 어느 수가 왜 나쁜지 물으면 이 도구부터 씁니다.
- motifs(fen, san): fen에서 san을 두면 생기는 전술 모티프(포크, 핀, 디스커버드 어택, 무방비 \
기물, 백랭크 등).
- maia_probs(fen, rating?): 그 레이팅대 사람이 각 수를 둘 확률. 학생의 수가 얼마나 자연스러운지 \
말할 때 씁니다.
- features(fen): 폰 구조 분류와 양쪽의 정적 특징(킹 안전, 기물 활동, 공간, 약점, 통과폰).

## 학생이 "왜 X는 안 좋은 수냐"고 반론할 때
1. compare(그 국면 FEN, 추천수, X)를 호출합니다. X가 합법이 아니면 그렇게 말하고 끝냅니다. \
학생이 보드에서 직접 둔 수는 메시지 첫 줄에 FEN과 함께 붙어 옵니다.
2. 첫 문장에서 결론을 말합니다: 등급과 핵심 이유. 승률 손실이 작으면(good 이하) "사실 큰 \
차이가 없다"고 인정하고 미세한 차이만 설명합니다.
3. X를 둔 뒤 상대의 최선 응수 줄을 show_board로 한 수씩 재생하며, 각 수가 무엇을 노리는지 \
caption과 본문에 적습니다. 보통 2~4수면 충분합니다.
4. 무너지는 이유를 짚습니다. 전술이면 motifs로 모티프를 확인하고, 포지션이면 compare의 특징 \
차이표에서 달라진 항목을 씁니다.
5. X의 의도(무엇을 노렸는지)를 인정한 뒤, 추천수가 같은 목표를 어떤 비용 없이 이루는지 \
대조합니다. 필요하면 추천수 줄도 show_board로 보여줍니다.
6. 학생이 "그럼 Y로 막으면?"이라 재반론하면 그 국면에서 1번부터 다시 합니다.

일반 질문(왜 이 수가 실수인가, 이 국면의 계획은 무엇인가)에도 같은 원칙입니다: 도구로 확인하고, \
보드를 움직이며, 확인된 것만 말합니다.

## 답변 형식
- 보드와 글을 번갈아 냅니다. 확인용 도구(compare, analyse, motifs)를 먼저 부른 뒤에는, \
show_board를 한 번 부를 때마다 바로 그 장면을 설명하는 문단을 한 개 씁니다. 도구를 전부 부른 \
다음 글을 한꺼번에 쓰지 않습니다. 첫 문단(결론)은 첫 보드보다 먼저 씁니다.
- 짧은 문단 2~5개. 마크다운 제목, 표, 굵은 글씨는 쓰지 않습니다. 목록은 수순을 나열할 때만 \
씁니다.
- 도구 이름이나 "도구를 호출했다"는 말은 하지 않습니다. "엔진 분석에 따르면", "깊이 16에서는"처럼 \
말합니다.
- 얕은 깊이의 엔진 판단은 틀릴 수 있으니 depth를 밝히며 조심스럽게 말합니다.
"""


def _strip_claims(value: Any) -> Any:
    """Verifier internals (claims) are not for the model; drop them everywhere."""
    if isinstance(value, dict):
        return {k: _strip_claims(v) for k, v in value.items() if k != "claims"}
    if isinstance(value, list):
        return [_strip_claims(v) for v in value]
    return value


def _label(ply: int, san: str) -> str:
    number = (ply + 1) // 2
    return f"{number}. {san}" if ply % 2 == 1 else f"{number}... {san}"


def moves_text(moves: list[schemas.MoveAnalysis], start: int, end: int) -> str:
    """'19. Nd5 Qc6 20. Qe2 Qd7' for plies start..end (1-based, inclusive); a black move that
    opens the span gets its own number."""
    parts: list[str] = []
    for move in moves:
        if not start <= move.ply <= end:
            continue
        if move.ply % 2 == 1:
            parts.append(f"{(move.ply + 1) // 2}. {move.san}")
        elif not parts:
            parts.append(_label(move.ply, move.san))
        else:
            parts.append(move.san)
    return " ".join(parts)


def facts(
    game: models.Game, analysis: schemas.GameAnalysis, review: schemas.MoveReviewOut, rating: int
) -> dict[str, Any]:
    ply = review.ply
    best = next((a.san for a in review.alternatives if a.is_best), None)
    if best is None and review.comparison is not None:
        best = review.comparison.a_san
    positions: dict[str, str] = {
        "before": review.fen_before,
        "after": review.fen_after,
    }
    punished_fen: str | None = None
    if review.refutation is not None and review.refutation.main_line:
        import chess

        board = chess.Board(review.fen_after)
        try:
            board.push_san(review.refutation.main_line[0])
            punished_fen = board.fen()
        except ValueError:
            punished_fen = None
    if punished_fen:
        positions["punished"] = punished_fen
    human: dict[str, Any] | None = None
    if review.human is not None:
        h = review.human.model_dump(mode="json")
        probs = sorted(h.get("move_probs", {}).items(), key=lambda kv: -kv[1])[:TOP_PROBS]
        h["move_probs"] = {san: round(p, 3) for san, p in probs}
        human = h
    strategy: dict[str, Any] | None = None
    if review.strategy is not None:
        s = review.strategy.model_dump(mode="json")
        strategy = {
            k: s.get(k)
            for k in ("structure", "your_move", "plans", "counterfactual")
            if s.get(k) is not None
        }
    explanation = review.explanation.model_dump(mode="json")
    stripped: dict[str, Any] = _strip_claims(
        {
            "game": {
                "white": game.white,
                "black": game.black,
                "white_elo": game.white_elo,
                "black_elo": game.black_elo,
                "result": game.result,
                "opening": game.opening_name,
                "eco": game.eco,
                "time_control": game.time_control,
                "student_color": game.user_color,
            },
            "student": {"rating_for_maia": rating},
            "move": {
                "ply": ply,
                "label": move_label(review.fen_before, review.san),
                "san": review.san,
                "color": review.color,
                "classification": review.classification,
                "eval_before": review.eval_before.model_dump(mode="json"),
                "eval_after": review.eval_after.model_dump(mode="json"),
                "engine_best": best,
            },
            "positions": positions,
            "positions_note": (
                "before = 이 수를 두기 직전(학생이 다른 수를 둘 수 있던 국면), "
                "after = 이 수를 둔 뒤, punished = 반박 첫 수까지 둔 뒤"
            ),
            "moves_before": moves_text(analysis.moves, max(1, ply - CONTEXT_PLIES_BEFORE), ply),
            "moves_after": moves_text(analysis.moves, ply + 1, ply + CONTEXT_PLIES_AFTER),
            "refutation": review.refutation.model_dump(mode="json") if review.refutation else None,
            "alternatives": [a.model_dump(mode="json") for a in review.alternatives],
            "comparison": review.comparison.model_dump(mode="json") if review.comparison else None,
            "human": human,
            "explanation": {
                "headline": explanation.get("headline"),
                "lead": explanation.get("lead"),
                "sentences": explanation.get("sentences"),
                "verified": explanation.get("verified"),
            },
            "strategy": strategy,
            "motifs_of_played_move": [m.model_dump(mode="json") for m in review.motifs],
        }
    )
    return stripped


def build_system_prompt(
    game: models.Game, analysis: schemas.GameAnalysis, review: schemas.MoveReviewOut, rating: int
) -> str:
    body = json.dumps(facts(game, analysis, review, rating), ensure_ascii=False)
    return f"{ROLE}\n<facts>\n{body}\n</facts>\n"
