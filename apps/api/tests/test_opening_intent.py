"""Move intent: what the deterministic detectors say about one move, and POST /openings/annotate.

Positions are written as move lists so the expectations read like the lines they come from. The
engine is stubbed (`play_coach.check`) everywhere except one end-to-end test, because these
tests are about which facts a move produces, not about what Stockfish thinks of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import chess
import pytest
from fastapi.testclient import TestClient

from chess_tutor.engine import EngineBusy, find_stockfish
from chess_tutor.schemas import AnnotateRequest, MoveAnnotation, MoveFact, PlayCheckResponse, Score
from chess_tutor.services import maia as maia_service
from chess_tutor.services import opening_intent, play_coach
from chess_tutor.services.verbalize import unclaimed_squares

needs_engine = pytest.mark.skipif(find_stockfish() is None, reason="stockfish binary not available")

RUY = "e4 e5 Nf3 Nc6 Bb5 a6"
OKELLY = "e4 c5 Nf3 a6 d4 cxd4 Nxd4 Nf6 Nc3 d6"
NAJDORF = "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6"
"""Two move orders into the same Najdorf position (tests/test_openings_map.py)."""
CARLSBAD = "r1bq1rk1/pp1nbppp/2p2n2/3p4/3P4/2N1PN2/PPQ1BPPP/R1B2RK1 w - - 0 10"
"""Carlsbad structure after the exchange, White to move and b4 available."""
QUIET = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
"""Rooks and kings only: every rook shuffle is legal, off book, and worth an engine call."""


class StubBackend:
    """Fixed move probabilities, so 'naturalness' does not depend on the machine."""

    name: maia_service.Source = "maia"

    def __init__(self, probs: dict[str, float]) -> None:
        self.probs = probs

    def is_available(self) -> bool:
        return True

    def move_probs(
        self,
        fen: str,
        rating: int,
        include: Iterable[str] = (),
        opp_rating: int | None = None,
    ) -> dict[str, float]:
        return dict(self.probs)


class StubCheck:
    """Stands in for play_coach.check and counts how often the engine was asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, req: object) -> PlayCheckResponse:
        san = getattr(req, "san", "")
        self.calls.append(str(san))
        return PlayCheckResponse(
            san=str(san),
            uci="0000",
            classification="inaccuracy",
            win_loss=0.031,
            eval_before=Score(cp=20),
            eval_after=Score(cp=-10),
            best_san="Ba4",
            best_uci="b5a4",
            pv=["Ba4"],
            reason="",
        )


@pytest.fixture
def engine_stub(monkeypatch: pytest.MonkeyPatch) -> StubCheck:
    stub = StubCheck()
    monkeypatch.setattr(play_coach, "check", stub)
    return stub


@pytest.fixture
def maia_stub() -> Iterator[None]:
    maia_service.use_backends(StubBackend({"e4": 0.42, "d4": 0.21}))
    yield
    maia_service.use_backends()


def line(moves: str, **kw: object) -> list[MoveAnnotation]:
    payload: dict[str, object] = {"moves_san": moves.split(), "engine": "off"}
    payload.update(kw)
    return opening_intent.annotate(AnnotateRequest.model_validate(payload))


def last(moves: str, **kw: object) -> MoveAnnotation:
    return line(moves, **kw)[-1]


def kinds(annotation: MoveAnnotation) -> set[str]:
    return {fact.kind for fact in annotation.facts}


def fact_of(annotation: MoveAnnotation, kind: str) -> MoveFact:
    found = next((f for f in annotation.facts if f.kind == kind), None)
    assert found is not None, f"no {kind} fact in {[f.kind for f in annotation.facts]}"
    return found


def has_claim(fact: MoveFact, kind: str, subject: str, obj: str | None = None) -> bool:
    return any(
        claim.kind == kind and claim.subject == subject and (obj is None or claim.object == obj)
        for claim in fact.claims
    )


# ---------- the facts of one move ----------


def test_the_first_move_takes_the_centre() -> None:
    annotation = last("e4")
    centre = fact_of(annotation, "center")
    assert "d5" in centre.text and "통제" in centre.text
    assert has_claim(centre, "attacks", "e4", "d5")
    assert has_claim(centre, "attacks", "e4", "f5")
    assert annotation.label == "1.e4" and annotation.in_book
    assert annotation.name_after == "King's Pawn Game"
    assert annotation.text.startswith("'King's Pawn Game'")


def test_a_developing_move_that_attacks_says_both_in_one_sentence() -> None:
    """Plan §5.3: centre and development merge into one sentence."""
    annotation = last("e4 e5 Nf3")
    development = fact_of(annotation, "development")
    assert development.text == "e5 폰을 공격하며 첫 번째 기물을 전개합니다."
    assert has_claim(development, "attacks", "f3", "e5")
    assert has_claim(development, "piece_on", "e5", "p")
    assert "center" not in kinds(annotation)


def test_a_defending_knight_is_said_to_defend() -> None:
    development = fact_of(last("e4 e5 Nf3 Nc6"), "development")
    assert development.text == "e5 폰을 방어하며 첫 번째 기물을 전개합니다."
    assert has_claim(development, "defends", "c6", "e5")


def test_the_ruy_lopez_bishop_attacks_the_knight_and_names_the_opening() -> None:
    annotation = last("e4 e5 Nf3 Nc6 Bb5")
    assert annotation.name_after == "Ruy Lopez"
    assert "Ruy Lopez" in fact_of(annotation, "name").text
    motif = fact_of(annotation, "motif")
    assert motif.text == "c6 나이트를 공격합니다."
    assert has_claim(motif, "attacks", "b5", "c6")
    assert has_claim(motif, "piece_on", "c6", "n")


def test_an_off_book_move_lists_the_book_moves_it_could_have_been() -> None:
    annotation = last(f"{RUY} Bc4")
    assert not annotation.in_book
    assert [c.san for c in annotation.book_alternatives] == ["Bxc6", "Ba4"]
    assert len(annotation.book_alternatives) <= opening_intent.BOOK_ALTERNATIVES
    book = fact_of(annotation, "book")
    assert "책 밖" in book.text and "Bxc6" in book.text and "Ba4" in book.text
    assert has_claim(book, "legal_move", None, "Bxc6")
    assert has_claim(book, "legal_move", None, "Ba4")


def test_a_book_move_has_no_alternatives_and_no_warning() -> None:
    annotation = last(f"{RUY} Bxc6")
    assert annotation.in_book and annotation.book_alternatives == []
    assert "book" not in kinds(annotation)
    assert annotation.name_after == "Ruy Lopez: Exchange Variation"


def test_a_fianchetto_is_prepared_and_then_completed() -> None:
    annotations = line("Nf3 d5 g3 Nf6 Bg2")
    prepared = fact_of(annotations[2], "fianchetto")
    assert prepared.text == "g3로 f1 비숍의 피안케토를 준비합니다."
    assert has_claim(prepared, "piece_on", "g3", "P")
    assert has_claim(prepared, "piece_on", "f1", "B")
    placed = fact_of(annotations[4], "fianchetto")
    assert placed.text == "비숍을 g2의 긴 대각선에 놓습니다."
    assert has_claim(placed, "piece_on", "g2", "B")


def test_a_pawn_advance_against_a_pawn_makes_tension() -> None:
    annotation = last("d4 d5 c4")
    tension = fact_of(annotation, "tension")
    assert tension.text == "c4로 d5 폰에 긴장을 만듭니다."
    assert has_claim(tension, "attacks", "c4", "d5")
    assert has_claim(tension, "piece_on", "d5", "p")
    assert "center" not in kinds(annotation), "the centre sentence would only repeat it"


def test_the_kings_gambit_offers_a_pawn_nothing_defends() -> None:
    annotation = last("e4 e5 f4")
    gambit = fact_of(annotation, "gambit")
    assert gambit.text.startswith("f4 폰을 내어 주는 수입니다.")
    assert has_claim(gambit, "attacks", "e5", "f4")
    assert has_claim(gambit, "piece_on", "f4", "P")
    assert "King's Gambit" in fact_of(annotation, "name").text


def test_a_transposition_is_named_as_one() -> None:
    okelly = last(OKELLY)
    assert okelly.transposition
    assert okelly.name_after == "Sicilian Defense: Najdorf Variation"
    assert fact_of(okelly, "transposition").text == "다른 수순으로 같은 국면에 합류합니다."
    direct = last(NAJDORF)
    assert not direct.transposition
    assert direct.name_after == "Sicilian Defense: Najdorf Variation"
    assert okelly.fen_after.split(" ")[0] == direct.fen_after.split(" ")[0]


def test_a_move_that_starts_a_structure_plan_says_which_plan() -> None:
    annotation = last("b4", start_fen=CARLSBAD)
    plan = fact_of(annotation, "plan")
    assert plan.text == "칼스바드 구조에서 '소수 공격 b4-b5' 계획의 수입니다."


def test_a_setup_advances_and_then_is_ruled_out() -> None:
    advanced = fact_of(last("d4 d5 Bf4"), "setup")
    assert advanced.text == "런던 시스템 배치 2/7."
    blocked = fact_of(last("d4 d5 e3"), "setup")
    assert blocked.text.startswith("이 수로 런던 시스템 배치는 물 건너갑니다.")
    assert "c1 비숍" in blocked.text


def test_castling_names_the_squares_the_king_and_rook_reach() -> None:
    castled = fact_of(last("e4 e5 Nf3 Nc6 Bb5 Nf6 O-O"), "castling")
    assert has_claim(castled, "piece_on", "g1", "K")
    assert has_claim(castled, "piece_on", "f1", "R")
    lost = fact_of(last("e4 e5 Ke2"), "castling")
    assert "캐슬링 권리를 잃습니다" in lost.text


def test_an_early_queen_is_flagged() -> None:
    sortie = fact_of(last("e4 e5 Qh5"), "development")
    assert sortie.text.startswith("퀸을 h5로 일찍 꺼냅니다.")
    assert has_claim(sortie, "piece_on", "h5", "Q")


# ---------- every sentence is checkable ----------


def test_every_sentence_is_verified_and_every_square_it_names_is_claimed() -> None:
    """Principle 1 and the rule services.verbalize follows: no square without a claim."""
    for moves in (f"{RUY} Bxc6 dxc6 O-O", OKELLY, "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6", "e4 e5 f4 exf4"):
        for annotation in line(moves):
            assert annotation.verified, (moves, annotation.label, annotation.facts)
            assert annotation.verified_claims == annotation.total_claims
            for fact in annotation.facts:
                assert fact.verified, (annotation.label, fact.kind, fact.text)
                assert unclaimed_squares(fact.text, fact.claims) == 0, (fact.kind, fact.text)
            spoken = [f.text for f in annotation.facts if f.verified]
            assert annotation.text == " ".join(spoken[: opening_intent.MAX_SENTENCES])


def test_the_facts_of_one_move_are_reachable_without_replaying_a_line() -> None:
    board = chess.Board()
    for san in "e4 e5".split():
        board.push_san(san)
    single = opening_intent.facts(board, board.parse_san("Nf3"))
    assert [fact.text for fact in single] == [f.text for f in last("e4 e5 Nf3").facts]


def test_the_journal_line_is_at_most_four_sentences() -> None:
    for annotation in line(f"{RUY} Bxc6 dxc6"):
        assert annotation.text.count("다.") <= opening_intent.MAX_SENTENCES


# ---------- maia and the engine ----------


def test_naturalness_is_only_asked_for_when_requested(maia_stub: None) -> None:
    quiet = last("e4")
    assert quiet.naturalness is None
    assert "naturalness" not in kinds(quiet)
    asked = last("e4", naturalness=True)
    assert asked.naturalness == pytest.approx(0.42)
    assert "42%" in fact_of(asked, "naturalness").text


def test_the_engine_runs_off_book_only_by_default(engine_stub: StubCheck) -> None:
    annotations = line(f"{RUY} Bc4", engine="off_book")
    assert engine_stub.calls == ["Bc4"]
    assert [a.san for a in annotations if a.engine is not None] == ["Bc4"]
    verdict = annotations[-1]
    assert verdict.engine is not None and verdict.engine.classification == "inaccuracy"
    engine_fact = fact_of(verdict, "engine")
    assert "부정확" in engine_fact.text and "Ba4" in engine_fact.text
    assert has_claim(engine_fact, "legal_move", None, "Ba4")


def test_engine_always_asks_for_every_move_and_off_asks_for_none(engine_stub: StubCheck) -> None:
    line("e4 e5 Nf3", engine="always")
    assert engine_stub.calls == ["e4", "e5", "Nf3"]
    engine_stub.calls.clear()
    line(f"{RUY} Bc4", engine="off")
    assert engine_stub.calls == []


def test_the_engine_is_asked_at_most_six_times_per_request(engine_stub: StubCheck) -> None:
    """A strip jump can send twenty moves; the cap is what keeps it under a second."""
    shuffle = "Ra2 Ra7 Ra3 Ra6 Ra4 Ra5 Rb4 Rb5 Rc4 Rc5"
    annotations = line(shuffle, start_fen=QUIET, engine="always")
    assert len(annotations) == 10
    assert len(engine_stub.calls) == opening_intent.ANNOTATE_ENGINE_CAP == 6
    assert [a.engine is not None for a in annotations][:6] == [True] * 6
    assert all(a.engine is None for a in annotations[6:])
    assert all(a.facts for a in annotations[6:]), "the facts are free; only the engine is capped"


@needs_engine
def test_a_real_engine_verdict_reaches_the_annotation() -> None:
    annotation = last(f"{RUY} Bc4", engine="off_book", depth=8)
    assert annotation.engine is not None
    assert annotation.engine.best_san
    assert "engine" in kinds(annotation)


# ---------- the endpoint ----------


def test_annotate_endpoint_returns_one_entry_per_move(client: TestClient) -> None:
    res = client.post(
        "/openings/annotate", json={"moves_san": ["e4", "e5", "Nf3"], "engine": "off"}
    )
    assert res.status_code == 200, res.text
    annotations = res.json()["annotations"]
    assert [a["label"] for a in annotations] == ["1.e4", "1…e5", "2.Nf3"]
    assert all(a["verified"] for a in annotations)
    assert annotations[0]["facts"][0]["kind"] == "name"


def test_annotate_endpoint_refuses_an_illegal_move(client: TestClient) -> None:
    res = client.post("/openings/annotate", json={"moves_san": ["e4", "e5", "Qxh8"]})
    assert res.status_code == 422
    assert "Qxh8" in res.json()["detail"]


def test_annotate_endpoint_answers_503_when_the_engine_cannot_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(_req: object) -> PlayCheckResponse:
        raise EngineBusy

    monkeypatch.setattr(play_coach, "check", busy)
    res = client.post("/openings/annotate", json={"moves_san": ["e4"], "engine": "always"})
    assert res.status_code == 503
    assert "engine=off" in res.json()["detail"]


def test_annotate_endpoint_refuses_an_impossible_position(client: TestClient) -> None:
    res = client.post(
        "/openings/annotate",
        json={"start_fen": "4k3/4R3/8/8/8/8/8/4K3 w - - 0 1", "moves_san": ["Rf7"]},
    )
    assert res.status_code == 422
