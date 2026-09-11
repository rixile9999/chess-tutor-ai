"""One Korean sentence plus the board facts it states, and the verification gate over a list.

Principle 1 (PLAN.md): the user only reads statements the verifier confirmed. Every module that
writes prose from detectors builds `Sentence(text, claims)` objects and hands them to
`assemble`, which runs `verify.verify_all` on each one, keeps the sentences whose claims all
hold and reports the tally the API sends back (`verified`, `verified_claims`, `total_claims`).

`play_coach` (hints, blunder checks) and `opening_intent` (the opening map's move commentary)
had a copy each of exactly this; M8d-5 merged them here. `opening_intent` also needs to know
per sentence whether it held - it reports every detector's sentence as a `MoveFact` even when
the text drops it - so `assemble` returns the per-sentence verdicts next to the joined text.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from chess_tutor.verify import Claim, verify_all


class Sentence:
    """A piece of the Korean answer together with the board facts it states."""

    def __init__(self, text: str, claims: list[Claim] | None = None) -> None:
        self.text = text
        self.claims = list(claims or [])


@dataclass(frozen=True)
class Checked:
    """One sentence after the verifier: whether every claim held, and how many did."""

    sentence: Sentence
    verified: bool
    verified_claims: int
    total_claims: int


@dataclass(frozen=True)
class Assembly:
    """The text a caller shows and the tally it reports, plus each sentence's own verdict."""

    text: str
    verified: bool
    verified_claims: int
    total_claims: int
    checked: tuple[Checked, ...]


def check(sentence: Sentence) -> Checked:
    """Run the verifier over one sentence's claims. A sentence without claims always holds."""
    verdicts = verify_all(sentence.claims)
    holds = sum(1 for verdict in verdicts if verdict.holds)
    return Checked(sentence, holds == len(verdicts), holds, len(verdicts))


def assemble(sentences: Iterable[Sentence], *, limit: int | None = None) -> Assembly:
    """Join the sentences whose claims all hold, at most `limit` of them, in the given order.

    A sentence that does not verify is dropped from the text but still counted in the tally
    and still reported in `checked`, so a caller can show it as an unverified opinion."""
    kept: list[str] = []
    checked: list[Checked] = []
    verified_claims = 0
    total_claims = 0
    for sentence in sentences:
        result = check(sentence)
        checked.append(result)
        verified_claims += result.verified_claims
        total_claims += result.total_claims
        if result.verified and (limit is None or len(kept) < limit):
            kept.append(sentence.text)
    return Assembly(
        text=" ".join(kept),
        verified=verified_claims == total_claims,
        verified_claims=verified_claims,
        total_claims=total_claims,
        checked=tuple(checked),
    )
