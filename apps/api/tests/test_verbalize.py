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
            Alternative(
                san="Nxd5",
                eval=Score(cp=30),
                line=["exd5", "exd5", "Qd3", "Rd8"],
                is_best=True,
                why=reasoning.explain_alternative(
                    before, "Nxd5", pv_a, Score(cp=30), Score(cp=30), "black"
                ),
            ),
            Alternative(
                san="exd5",
                eval=Score(cp=60),
                line=["exd5", "Qd6", "Bf4"],
                why=reasoning.explain_alternative(
                    before, "exd5", pv_b, Score(cp=60), Score(cp=30), "black"
                ),
            ),
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
        strategy_note="20...Qd7는 이 구조의 계획 목록에 없는 수입니다",
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
    assert "21… Bxf6에는 22.Rxd7이 있어 퀸 상실로 끝납니다." in out.sentences
    assert "21… Kh8에는 22.Nxd7이 있어 같은 결과로 끝납니다." in out.sentences
    assert "왜 Nxe7+이 아니라 Nxf6+인가: Nxe7+에는 Qxe7이 있습니다." in out.sentences
    best = next(s for s in out.sentences if s.startswith("엔진 최선 20… Nxd5 (+0.3)"))
    assert "d5 나이트를 나이트로 잡습니다" in best and "Nxd5: Nxd5" not in best
    assert any(s.startswith("차선 20… exd5 (+0.6)") for s in out.sentences)
    assert any("Nxd5" in s and "앞섭니다" in s for s in out.sentences)
    assert "Maia 예측으로 1500 구간에서 20… Qd7을 두는 비율은 약 34%입니다." in out.sentences
    assert "구조는 오픈 센터입니다. 20… Qd7는 이 구조의 계획 목록에 없는 수입니다." in out.sentences
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
    # 7 claims plus one 'missing claim' for the sentence that names squares without any
    assert out.total_claims == 8 and out.verified_claims == 5
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
