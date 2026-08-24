"""An in-memory market-data provider.

Two uses, both real, and the same pair
:class:`~tcg_shared.storage.memory.InMemoryObjectStorage` was written for. It is
the reference implementation the port's contract test runs against — which is
what makes "swapping the provider requires no change to calling code" a
demonstrated property rather than a claim — and it is what lets M5's economic
engine be built and tested before any real provider exists. #52 implements the
selected vendor against the same contract.

It is shipped code rather than a test fixture for that second reason. The
in-memory :class:`~tcg_domain.repository.CardRepository` lives inside its test
file because nothing but that file wants a fake catalog; a fake price provider
is a dependency of a whole milestone.

What it cannot do is be a source of truth about the market: every figure in it
was put there by whoever constructed it. Its `provider` slug is ``memory`` for
the reason `InMemoryObjectStorage` mints ``memory://`` URLs — an observation
that reaches a database carrying it is visibly not a real price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tcg_domain.card import CardReference
from tcg_domain.confidence import InsufficientInformation, Uncertain
from tcg_domain.grade import Grade

from tcg_market_data.port import PriceObservation, validated_grade_key

__all__ = ["InMemoryMarketDataProvider"]

#: The slug this provider stamps observations with. Deliberately not the name of
#: any real vendor: a row carrying it is a row nobody should price a decision on.
_PROVIDER = "memory"


@dataclass(slots=True)
class InMemoryMarketDataProvider:
    """A provider backed by a list of observations.

    Args:
        observations: The observations it holds, if any. Supplied mostly so a
            test or a local run can arrange a market without a series of writes.
            There is no ``record()`` method because the port has no write side
            and ``provider.observations.append(...)`` already is one.
        provider: The slug this provider answers to.

    Note:
        "Latest" is the **last** element of a stable sort by ``observed_at``,
        not :func:`max`. `max` returns the *first* maximal element, so two
        observations at the same instant would resolve to whichever was inserted
        first — the opposite of the arrange-then-override behaviour a caller
        appending to `observations` expects.
    """

    observations: list[PriceObservation] = field(default_factory=list)
    provider: str = _PROVIDER

    def _matching(
        self, card: CardReference, company: str | None, grade: Grade | None
    ) -> list[PriceObservation]:
        """Every held observation for one card and one market key, oldest first.

        Comparing `grading_company` against `None` is what stops a graded
        observation answering a raw question, which is the one way a fake like
        this quietly reports a PSA 10 price as a card's raw value.
        """
        return sorted(
            (
                observation
                for observation in self.observations
                if observation.card == card
                and observation.grading_company == company
                and observation.grade == grade
            ),
            key=lambda observation: observation.observed_at,
        )

    async def get_raw_price(self, card: CardReference) -> Uncertain[PriceObservation]:
        matches = self._matching(card, None, None)
        if not matches:
            return InsufficientInformation(reason=f"no raw price is held for {card}")
        return matches[-1]

    async def get_graded_price(
        self, card: CardReference, company: str, grade: Grade
    ) -> Uncertain[PriceObservation]:
        # Validated before the lookup, so an illegal (company, grade) pair is
        # refused rather than answered "unavailable" — a PSA 9.5 does not exist,
        # which is a different fact from nobody having priced one.
        slug = validated_grade_key(company, grade)
        matches = self._matching(card, slug, grade)
        if not matches:
            return InsufficientInformation(reason=f"no {slug} {grade} price is held for {card}")
        return matches[-1]

    async def get_price_history(self, card: CardReference) -> tuple[PriceObservation, ...]:
        return tuple(
            sorted(
                (observation for observation in self.observations if observation.card == card),
                key=lambda observation: observation.history_key,
            )
        )
