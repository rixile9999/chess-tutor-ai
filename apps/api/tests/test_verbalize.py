"""Verbalization (layer 4): template prose from review facts, the verifier gate, and the LLM
path without ever calling the API. Facts are hand-built from the mockup position, so no
engine is needed here."""

from __future__ import annotations

from collections.abc import Iterator

import chess
import pytest

from chess_tutor.config import get_settings
from chess_tutor.motifs import detect
from chess_tutor.schemas import (
    Alternative,
    Branch,
    Comparison,
    FeatureDiffRow,
    HumanView,
    MotifOut,
    Refutation,
    Score,
)
from chess_tutor.services import maia, reasoning, verbalize
from chess_tutor.services.verbalize import (
    LLMClaim,
    LLMOutput,
    LLMSentence,
    ReviewFacts,
    fmt_score,
    josa,
    move_label,
    numbered_line,
)
from chess_tutor.verify import Claim, verify

BEFORE = "5rk1/p3bppp/1pq1pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 b - - 4 20"
AFTER = "5rk1/p2qbppp/1p2pn2/3N4/4P3/4B3/PP2QPPP/3R2K1 w - - 5 21"
PUNISHED = "5rk1/p2qbppp/1p2pN2/8/4P3/4B3/PP2QPPP/3R2K1 b - - 0 21"


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    yield


def _moves(fen: str, sans: str) -> list[chess.Move]:
    board = chess.Board(fen)
    out = []
    for san in sans.split():
        move = board.parse_san(san)
        out.append(move)
        board.push(move)
    return out


def _motifs(fen: str, san: str) -> list[MotifOut]:
    board = chess.Board(fen)
    return [MotifOut.model_validate(m.as_dict()) for m in detect(board, board.parse_san(san))]


def _alternative(
    board: chess.Board,
    san: str,
    pv: list[chess.Move],
    eval: Score,
    line: list[str],
    is_best: bool,
) -> Alternative:
    """An Alternative the way services.review builds it: prose and claims from reasoning."""
    why, claims = reasoning.explain_alternative(board, san, pv, eval, Score(cp=30), "black")
    return Alternative(san=san, eval=eval, line=line, is_best=is_best, why=why, claims=claims)


def mock_facts(**overrides: object) -> ReviewFacts:
    """20...Qd7?? from the mockup, with the refutation, branches, alternatives, comparison
    and human view a review would attach."""
    before = chess.Board(BEFORE)
    reason, claims = maia.natural_reason(before, before.parse_san("Qd7"), last_san="Nd5")
    pv_a = _moves(BEFORE, "Nxd5 exd5 exd5 Qd3")
    pv_b = _moves(BEFORE, "exd5 exd5 Qd6 Bf4")
    facts = ReviewFacts(
        game_id=1,
        ply=1,
        san="Qd7",
        uci="c6d7",
        color="black",
        move_label="20… Qd7",
        fen_before=BEFORE,
        fen_after=AFTER,
        classification="blunder",
        eval_before=Score(cp=40),
        eval_after=Score(cp=560),
        best_san="Nxd5",
        natural_reason=reason,
        natural_claims=claims,
        refutation=Refutation(
            main_line=["Nxf6+", "Bxf6", "Rxd7"],
            branches=[
                Branch(moves=["Bxf6", "Rxd7"], result="퀸 상실", eval=Score(cp=560)),
                Branch(moves=["gxf6", "Rxd7"], result="같은 결과", eval=Score(cp=600)),
                Branch(moves=["Kh8", "Nxd7"], result="같은 결과", eval=Score(cp=780)),
            ],
            motifs=_motifs(AFTER, "Nxf6+"),
            note="왜 Nxe7+이 아니라 Nxf6+인가: Nxe7+에는 Qxe7이 있습니다.",
        ),
        punish_label="21. Nxf6+",
        fen_punished=PUNISHED,
        note_line=["Nxe7+", "Qxe7"],
        alternatives=[
            _alternative(before, "Nxd5", pv_a, Score(cp=30), ["exd5", "exd5", "Qd3", "Rd8"], True),
            _alternative(before, "exd5", pv_b, Score(cp=60), ["exd5", "Qd6", "Bf4"], False),
        ],
        comparison=reasoning.compare_moves(
            before, "Nxd5", "exd5", pv_a, pv_b, "black", Score(cp=30), Score(cp=60)
        ),
        human=HumanView(
            rating=1500,
            move_probs={"Qd7": 0.34, "Nxd5": 0.4},
            played_prob=0.34,
            natural_reason=reason,
            computer_move=False,
            source="maia",
            claims=claims,
        ),
        strategy_note="20...Qd7은 이 구조의 계획 목록에 없는 수입니다",
        structure_name="오픈 센터",
        positions={"before": BEFORE, "after": AFTER, "punished": PUNISHED},
    )
    return facts.model_copy(update=overrides)


# ---------- helpers ----------


def test_particles_follow_the_last_syllable_or_digit() -> None:
    assert josa("Nxf6+", "이") == "Nxf6+이"  # 6 = 육
    assert josa("Qd7", "을") == "Qd7을"  # 7 = 칠
    assert josa("Bc5", "이") == "Bc5가"  # 5 = 오
    assert josa("퀸 상실", "로") == "퀸 상실로"  # ㄹ takes 로
    assert josa("기물 손실", "과") == "기물 손실과"
    assert josa("Rd1", "과") == "Rd1과"
    assert josa("Kg8", "은") == "Kg8은"
    assert josa("O-O", "을") == "O-O를"


def test_labels_lines_and_scores() -> None:
    assert move_label(BEFORE, "Qd7") == "20… Qd7"
    assert move_label(AFTER, "Nxf6+") == "21. Nxf6+"
    text, claims = numbered_line(AFTER, ["Nxf6+", "Bxf6", "Rxd7"])
    assert text == "21.Nxf6+ Bxf6 22.Rxd7"
    assert [c.object for c in claims] == ["Nxf6+", "Bxf6", "Rxd7"]
    assert claims[1].fen == PUNISHED
    text, claims = numbered_line(PUNISHED, ["Bxf6", "Rxd7"])
    assert text == "21...Bxf6 22.Rxd7"
    # an illegal continuation keeps its (failing) claim so the sentence is dropped later
    text, claims = numbered_line(AFTER, ["Nxf6+", "Qxf6"])
    assert text == "21.Nxf6+" and len(claims) == 2
    assert fmt_score(Score(cp=40)) == "+0.4"
    assert fmt_score(Score(cp=-215)) == "-2.1"
    assert fmt_score(Score(mate=3)) == "#3"


# ---------- template ----------


def test_template_headline_and_lead_follow_the_mockup() -> None:
    out = verbalize.template_explanation(mock_facts())
    assert out.source == "template"
    assert out.headline == "20… Qd7 블런더"
    assert out.lead.startswith("Nd5가 e7 비숍을 공격하자 퀸으로 지켰습니다.")
    assert "Qd7이 Rd1과 같은 d파일에 놓입니다" in out.lead
    assert "Nd5가 사이를 가리고 있을 뿐입니다" in out.lead
    assert out.verified is True
    assert out.total_claims > 0
    assert out.verified_claims == out.total_claims == len(out.claims)
    for text in [out.headline, out.lead, *out.sentences]:
        assert "—" not in text


def test_template_sentences_cover_refutation_branches_and_alternatives() -> None:
    out = verbalize.template_explanation(mock_facts())
    joined = "\n".join(out.sentences)
    assert out.sentences[0].startswith("21. Nxf6+: 체크를 주면서 비켜서고")
    assert "Rd1이 Qd7을 겨냥합니다" in out.sentences[0]
    assert "흑은 체크부터 처리해야 합니다" in out.sentences[0]
    assert "나이트 포크로 Qd7과 Kg8을 동시에 공격합니다" in joined
    assert "이어지는 수순: 21.Nxf6+ Bxf6 22.Rxd7." in out.sentences
    # the Bxf6 branch is the main line word for word, so it is not repeated; the branch that
    # takes its place says the outcome instead of pointing back at a sentence nobody saw
    assert not any(s.startswith("21… Bxf6") for s in out.sentences)
    assert "21… gxf6에는 22.Rxd7이 있어 퀸 상실로 끝납니다." in out.sentences
    assert "21… Kh8에는 22.Nxd7이 있어 같은 결과로 끝납니다." in out.sentences
    assert sum(s.endswith("끝납니다.") for s in out.sentences) == verbalize.BRANCH_SENTENCES
    assert "왜 Nxe7+이 아니라 Nxf6+인가: Nxe7+에는 Qxe7이 있습니다." in out.sentences
    best = next(s for s in out.sentences if s.startswith("엔진 최선 20… Nxd5 (+0.3)"))
    assert "d5 나이트를 나이트로 잡습니다" in best and "Nxd5: Nxd5" not in best
    assert "백이 exd5로 받아도 흑이 exd5로 되잡습니다" in best
    assert any(s.startswith("차선 20… exd5 (+0.6)") for s in out.sentences)
    assert "Nxd5가 기물 활동과 공간에서 앞섭니다. 엔진 평가는 Nxd5 +0.3, exd5 +0.6입니다." in (
        out.sentences
    )
    assert "Maia 예측으로 1500 구간에서 20… Qd7을 두는 비율은 약 34%입니다." in out.sentences
    assert "구조는 오픈 센터입니다. 20… Qd7은 이 구조의 계획 목록에 없는 수입니다." in out.sentences
    # the board facts behind the first sentence are all there for the verifier
    kinds = {(c.kind, c.subject, c.object) for c in out.claims if c.fen == PUNISHED}
    assert ("attacks", "d1", "d7") in kinds
    assert ("is_check", None, None) in kinds
    assert ("attacks", "f6", "g8") in kinds


def test_illegal_line_drops_only_its_sentence() -> None:
    facts = mock_facts()
    assert facts.refutation is not None
    broken = facts.refutation.model_copy(update={"main_line": ["Nxf6+", "Qxf6", "Rxd7"]})
    out = verbalize.template_explanation(facts.model_copy(update={"refutation": broken}))
    assert not any(s.startswith("이어지는 수순") for s in out.sentences)
    assert any(s.startswith("21. Nxf6+: 체크를 주면서") for s in out.sentences)
    assert out.verified is False
    assert out.verified_claims < out.total_claims
    assert all(c.holds for c in verbalize._verdicts(out.claims))


def test_wrong_square_in_a_motif_drops_its_sentence() -> None:
    facts = mock_facts()
    assert facts.refutation is not None
    bad = facts.refutation.motifs[0].model_copy(update={"attacker": "d2"})
    refutation = facts.refutation.model_copy(update={"motifs": [bad, *facts.refutation.motifs[1:]]})
    out = verbalize.template_explanation(facts.model_copy(update={"refutation": refutation}))
    assert not any("체크를 주면서 비켜서고" in s for s in out.sentences)
    assert any("나이트 포크로" in s for s in out.sentences)
    assert out.lead.startswith("Nd5가 e7 비숍을 공격하자")
    assert out.verified is False


def test_quiet_best_move_without_refutation() -> None:
    facts = mock_facts(
        san="Nxd5",
        uci="f6d5",
        move_label="20… Nxd5",
        classification="best",
        eval_before=Score(cp=30),
        eval_after=Score(cp=30),
        natural_reason=None,
        natural_claims=[],
        refutation=None,
        punish_label=None,
        fen_punished=None,
        note_line=[],
        human=None,
        strategy_note=None,
        comparison=Comparison(
            a_san="Nxd5",
            b_san="exd5",
            rows=[FeatureDiffRow(feature="통과폰", a="흑 d5", b="백 d5", delta=2.0)],
            summary="Nxd5는 통과폰에서 앞섭니다.",
        ),
    )
    out = verbalize.template_explanation(facts)
    assert out.headline == "20… Nxd5 최선"
    assert out.lead == "이 수 뒤 평가는 +0.3입니다. 엔진 최선 수와 같습니다."
    assert any(s.startswith("차선 20… exd5") for s in out.sentences)
    assert not any(s.startswith("엔진 최선 20… Nxd5") for s in out.sentences)
    assert "Nxd5는 통과폰에서 앞섭니다." in out.sentences
    assert out.verified is True and out.total_claims > 0


def test_best_move_that_is_not_the_multipv_best_names_the_engine_move() -> None:
    """The confirmation search can rate the played move as best while the shallow MultiPV
    still names another move, so the lead must not claim the two are the same move."""
    facts = mock_facts(
        san="exd5",
        uci="e6d5",
        move_label="20… exd5",
        classification="best",
        eval_before=Score(cp=30),
        eval_after=Score(cp=30),
        natural_reason=None,
        natural_claims=[],
        refutation=None,
        punish_label=None,
        fen_punished=None,
        note_line=[],
        human=None,
        strategy_note=None,
    )
    out = verbalize.template_explanation(facts)
    assert out.lead == "이 수 뒤 평가는 +0.3입니다. 엔진 최선 Nxd5와 차이가 없습니다."
    assert "엔진 최선 수와 같습니다." not in out.lead
    assert out.verified is True and out.total_claims > 0


def test_tiny_probabilities_read_as_under_one_percent() -> None:
    facts = mock_facts()
    assert facts.human is not None
    facts = facts.model_copy(
        update={"human": facts.human.model_copy(update={"played_prob": 0.0004})}
    )
    out = verbalize.template_explanation(facts)
    assert any(s.endswith("두는 비율은 1% 미만입니다.") for s in out.sentences)


# ---------- LLM path (never calls the API) ----------


def test_llm_explanation_is_none_without_a_key_and_explain_uses_the_template() -> None:
    facts = mock_facts()
    assert verbalize.llm_explanation(facts) is None
    out = verbalize.explain(facts)
    assert out.source == "template" and out.verified


def test_llm_output_is_verified_and_failing_sentences_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    canned = LLMOutput(
        headline="20… Qd7 블런더",
        lead="퀸이 d파일에 서면서 Rd1과 같은 선에 놓입니다 — 지금은 Nd5가 가릴 뿐입니다.",
        lead_claims=[
            LLMClaim(kind="piece_on", position="after", subject="d7", object="q"),
            LLMClaim(kind="piece_on", position="after", subject="d1", object="R"),
            LLMClaim(kind="piece_on", position="after", subject="d5", object="N"),
        ],
        sentences=[
            LLMSentence(
                text="21.Nxf6+로 나이트가 비켜서면 Rd1이 Qd7을 겨냥합니다.",
                claims=[
                    LLMClaim(kind="legal_move", position="after", subject=None, object="Nxf6+"),
                    LLMClaim(kind="attacks", position="punished", subject="d1", object="d7"),
                ],
            ),
            LLMSentence(
                text="Rd1은 지금도 Qd7을 공격합니다.",
                claims=[LLMClaim(kind="attacks", position="after", subject="d1", object="d7")],
            ),
            LLMSentence(
                text="Bxf6 뒤 Rxd7로 퀸을 잃습니다.",
                claims=[
                    LLMClaim(kind="legal_move", position="nowhere", subject=None, object="Rxd7")
                ],
            ),
            LLMSentence(text="Rd1이 d7을 노립니다.", claims=[]),
            LLMSentence(
                text="흑은 체크부터 처리해야 합니다.",
                claims=[LLMClaim(kind="is_check", position="punished", subject=None, object=None)],
            ),
        ],
    )
    calls: list[ReviewFacts] = []

    def fake_call(facts: ReviewFacts) -> LLMOutput:
        calls.append(facts)
        return canned

    monkeypatch.setattr(verbalize, "_call_llm", fake_call)
    facts = mock_facts()
    out = verbalize.explain(facts)
    assert calls == [facts]
    assert out.source == "llm"
    assert out.headline == "20… Qd7 블런더"
    assert "—" not in out.lead and "Rd1과 같은 선에 놓입니다" in out.lead
    assert out.sentences == [
        "21.Nxf6+로 나이트가 비켜서면 Rd1이 Qd7을 겨냥합니다.",
        "흑은 체크부터 처리해야 합니다.",
    ]
    assert out.verified is False  # three sentences failed the gate
    # 8 claims, plus the unknown position of the Rxd7 sentence and one per square that no
    # claim covers: d7 in that same sentence, and d1/d7 in the one with no claims at all
    assert out.total_claims == 12 and out.verified_claims == 6
    assert out.lead == "퀸이 d파일에 서면서 Rd1과 같은 선에 놓입니다, 지금은 Nd5가 가릴 뿐입니다."
    assert all(c.holds for c in verbalize._verdicts(out.claims))


def test_llm_result_with_too_few_sentences_falls_back_to_the_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    thin = LLMOutput(
        headline="x",
        lead="",
        lead_claims=[],
        sentences=[
            LLMSentence(
                text="흑은 체크부터 처리해야 합니다.",
                claims=[LLMClaim(kind="is_check", position="punished", subject=None, object=None)],
            )
        ],
    )
    monkeypatch.setattr(verbalize, "_call_llm", lambda facts: thin)
    llm = verbalize.llm_explanation(mock_facts())
    assert llm is not None and llm.source == "llm" and len(llm.sentences) == 1
    assert llm.headline == "20… Qd7 블런더"  # a headline without the move is replaced
    assert verbalize.explain(mock_facts()).source == "template"


def test_llm_exception_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")

    def boom(facts: ReviewFacts) -> LLMOutput:
        raise RuntimeError("network down")

    monkeypatch.setattr(verbalize, "_call_llm", boom)
    assert verbalize.llm_explanation(mock_facts()) is None
    assert verbalize.explain(mock_facts()).source == "template"


# ---------- the square rule and the mate branch ----------

OPERA_PUNISHED = "4kb1r/p2n1ppp/4q3/4p1B1/4P3/1Q6/PPP2PPP/2KR4 w k - 0 16"
"""Opera game after 15...Nxd7: White to move and 16.Qb8+ Nxb8 17.Rd8# is mate."""


def test_unclaimed_squares_counts_every_square_no_claim_covers() -> None:
    assert verbalize.unclaimed_squares("Rd1이 d7을 노립니다.", []) == 2
    covered = [Claim(kind="attacks", fen=AFTER, subject="d1", object="d7")]
    assert verbalize.unclaimed_squares("Rd1이 d7을 노립니다.", covered) == 0
    # a legal_move claim covers both ends of the move it names
    moved = [Claim(kind="legal_move", fen=AFTER, object="Nxf6+")]
    assert verbalize.unclaimed_squares("21.Nxf6+로 d5 나이트가 비켜섭니다.", moved) == 0
    assert verbalize.unclaimed_squares("21.Nxf6+ 뒤 Rd1이 열립니다.", moved) == 1
    # a quoted plan title is a name, not a board claim
    assert verbalize.unclaimed_squares("계획 '...d5 브레이크'를 준비합니다.", []) == 0


def test_template_sentence_with_an_unclaimed_square_is_dropped() -> None:
    facts = mock_facts()
    assert facts.strategy_note is not None
    out = verbalize.template_explanation(
        facts.model_copy(update={"strategy_note": "20...Qd7 뒤 Nf3이 e5를 노립니다"})
    )
    assert not any("Nf3" in s for s in out.sentences)
    assert out.verified is False


def test_branch_that_mates_for_the_mover_is_not_framed_as_a_refutation() -> None:
    facts = mock_facts(
        fen_punished=OPERA_PUNISHED,
        refutation=Refutation(
            main_line=["Nxd7"],
            branches=[
                Branch(moves=["Qb8+", "Nxb8", "Rd8#"], result="메이트 승리", eval=Score(mate=2))
            ],
            motifs=[],
        ),
    )
    sentence = verbalize._branch_sentences(facts)[0]
    assert sentence.text == "16. Qb8+: 16...Nxb8 17.Rd8#로 메이트를 만듭니다."
    assert "퀸 상실" not in sentence.text and "끝납니다" not in sentence.text
    assert any(c.kind == "checkmate" for c in sentence.claims)
    assert all(v.holds for v in verbalize._verdicts(sentence.claims))
    assert verbalize.unclaimed_squares(sentence.text, sentence.claims) == 0


def test_checkmate_claim_is_not_satisfied_by_a_mere_check() -> None:
    # parse_san ignores the '#', so only a checkmate claim can back the word 메이트
    mated = chess.Board(OPERA_PUNISHED)
    for san in ("Qb8+", "Nxb8", "Rd8#"):
        mated.push_san(san)
    assert verify(Claim(kind="checkmate", fen=mated.fen())).holds
    check_only = chess.Board(OPERA_PUNISHED)
    check_only.push_san("Qb8+")
    assert not verify(Claim(kind="checkmate", fen=check_only.fen())).holds
    assert verify(Claim(kind="is_check", fen=check_only.fen())).holds


def test_natural_opener_is_dropped_with_the_sentence_it_refers_back_to() -> None:
    out = verbalize.template_explanation(mock_facts())
    assert "자연스러운 반응이지만" in out.lead
    liar = mock_facts(natural_claims=[Claim(kind="piece_on", fen=BEFORE, subject="d4", object="Q")])
    out = verbalize.template_explanation(liar)
    assert not any("Nd5가 e7 비숍을 공격하자" in s for s in [out.lead, *out.sentences])
    assert "자연스러운 반응이지만" not in out.lead
    assert out.lead.startswith("하지만 ")


def test_eval_lead_claims_the_engine_move_it_names() -> None:
    quiet = mock_facts(
        classification="inaccuracy",
        refutation=None,
        punish_label=None,
        fen_punished=None,
        note_line=[],
        natural_reason=None,
        natural_claims=[],
        human=None,
        strategy_note=None,
    )
    out = verbalize.template_explanation(quiet)
    assert out.lead.endswith("엔진 최선은 Nxd5입니다.")
    assert any(c.kind == "legal_move" and c.object == "Nxd5" for c in out.claims)
    # an engine move that is not legal there takes its clause down with it
    out = verbalize.template_explanation(quiet.model_copy(update={"best_san": "Nxa5"}))
    assert out.lead == "이 수 뒤 평가는 +5.6입니다."
    assert out.verified is False


def test_a_delivered_mate_is_not_reported_as_a_hundred_pawns() -> None:
    """analysis stores a mate as ±100 pawns; '평가는 +100.0입니다' is not a real evaluation."""
    mate_before = "4kb1r/p2n1ppp/4q3/4p1B1/4P3/1Q6/PPP2PPP/2KR4 w k - 0 16"
    board = chess.Board(mate_before)
    for san in ("Qb8+", "Nxb8"):
        board.push_san(san)
    fen_before = board.fen()
    board.push_san("Rd8#")
    facts = mock_facts(
        san="Rd8#",
        uci="d1d8",
        color="white",
        move_label="17. Rd8#",
        fen_before=fen_before,
        fen_after=board.fen(),
        classification="best",
        eval_before=Score(cp=10000),
        eval_after=Score(cp=10000),
        best_san="Rd8#",
        natural_reason=None,
        natural_claims=[],
        refutation=None,
        punish_label=None,
        fen_punished=None,
        note_line=[],
        alternatives=[],
        comparison=None,
        human=None,
        strategy_note=None,
    )
    out = verbalize.template_explanation(facts)
    assert out.lead == "이 수로 체크메이트입니다."
    assert "100" not in out.lead
    assert any(c.kind == "checkmate" for c in out.claims)
    assert out.verified is True


def test_threat_claims_check_the_mate_a_threat_sentence_names() -> None:
    fen = "6k1/5ppp/8/8/8/8/5PPP/3R2K1 b - - 1 1"  # after Rd1: Rd8 mates if Black passes
    claims = verbalize._threat_claims(fen, "Rd8#")
    assert [c.kind for c in claims] == ["legal_move", "checkmate"]
    assert all(v.holds for v in verbalize._verdicts(claims))
    # the same sentence about a move that does not mate fails, because parse_san ignores '#'
    assert not all(v.holds for v in verbalize._verdicts(verbalize._threat_claims(fen, "Rd4")))
