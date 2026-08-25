"""Spec §40's expected value: ``EV = Σ_g P(g)·V(g)``.

The distribution arrives as a :class:`~tcg_domain.distribution.GradeDistribution`
and the prices as a plain mapping, so this module cannot tell — and must never
ask — which model produced the one or which provider produced the other. That is
CLAUDE.md's master architectural rule, and ``tests/test_economic_engine_purity.py``
is what enforces it.

Three decisions are load-bearing, and each has a test named after it.

**A missing ``V(g)`` is never zero.** Valuing an unpriced grade at nothing drags
the expectation below every price in the ladder and can flip a recommendation
from `grade` to `do_not_grade` on a card whose only unknown is its best outcome.
An unpriced grade is excluded, the remainder renormalised, and the exclusion
recorded in :attr:`ExpectedValue.unpriced_grades` and
:attr:`ExpectedValue.unpriced_probability`. The figure is therefore the
expectation *conditional on a priced grade occurring*; nothing is reported when
no priced grade carries any probability at all.

**A bucket is worth the least it can be worth.** ``7_or_lower`` is spec §24's
own key and no market ladder ever prices it — the 55 pairs
``GET /cards/{id}/market`` serves are exact grades. It is valued at the lowest
price among the grades it covers, which is the bound the bucket actually
guarantees. See :func:`_resolve` for what that costs.

**The sum is exact and rounds once.** Terms are `Decimal` products accumulated
in full precision and turned into :class:`~tcg_domain.money.Money` at the end,
so the cent appears once rather than per grade. `Money` quantises on
construction, so multiplying per term would round eighteen times and answer a
cent high on a ladder whose grades are worth the same.

There is no selling fee here, and no cost of any kind: ADR 0007 applies
``sale_costs`` "per outcome, inside the sum, never to the expected value", which
a caller satisfies by netting each ``V(g)`` *before* handing the ladder over.
#60 owns that, and this function grows no fee parameter for it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from tcg_domain import (
    Confidence,
    Currency,
    CurrencyMismatch,
    Grade,
    GradeBound,
    GradeDistribution,
    InsufficientInformation,
    Money,
    Uncertain,
)

from tcg_economic_engine.errors import InvalidGradedPrice

__all__ = ["ExpectedValue", "GradedPrice", "expected_value"]

#: Why an expectation could not be formed. The only reason there is: every other
#: shortfall is a smaller answer plus a record of what was missing.
NO_PRICE = "no_graded_price_available"


@dataclass(frozen=True, slots=True)
class GradedPrice:
    """What one grade is worth, and how much that figure is trusted.

    The engine's own price type rather than
    :class:`tcg_market_data.port.PriceObservation`: importing that would tie the
    economics to a provider's shape, which is exactly what the purity test
    forbids. A caller carries the two fields across; the observation's card,
    provider and timestamp say nothing about what an expectation is worth.

    Args:
        value: The market value of the grade, in SGD. Never negative — a market
            price is not, and `SellingFee.on`'s cap means a netted one is not
            either.
        confidence: How far the price is trusted, already discounted for age.
            :func:`tcg_market_data.freshness.price_confidence` is what produces
            it; this package takes the number and does not recompute it, because
            age is a question asked at a moment and this is not that moment.

    Raises:
        InvalidGradedPrice: If `value` is not a non-negative `Money` or
            `confidence` is not a :class:`~tcg_domain.confidence.Confidence`.
    """

    value: Money
    confidence: Confidence

    def __post_init__(self) -> None:
        # Typed `object` at the guard for the reason `tcg_domain.money._quantised`
        # is: prices reach this from JSON payloads and untyped callers, so a
        # `Money` annotation would make the check below unreachable to the type
        # checker while it stays entirely reachable at runtime.
        value: object = self.value
        if not isinstance(value, Money):
            raise InvalidGradedPrice(
                f"a graded price must be Money, got {type(value).__name__}. "
                "Use Money.of('12.34') so the amount stays exact."
            )
        if value < Money.zero(value.currency):
            raise InvalidGradedPrice(f"a graded price must not be negative, got {value}")

        confidence: object = self.confidence
        if not isinstance(confidence, Confidence):
            raise InvalidGradedPrice(
                f"a price confidence must be a Confidence, got {type(confidence).__name__}. "
                "A bare float invites a confidence of 87 that means 87%."
            )

    def __str__(self) -> str:
        return f"{self.value} ({self.confidence})"


@dataclass(frozen=True, slots=True)
class ExpectedValue:
    """Spec §40's expectation, with what it could not see.

    Args:
        amount: ``Σ P(g)·V(g)`` over the priced grades, divided by their
            probability mass — the expectation **conditional on a priced grade
            occurring**. Equal to the unconditional expectation exactly when
            `unpriced_grades` is empty.
        confidence: The distribution's confidence times the probability-weighted
            confidence of the prices used. See :func:`expected_value`.
        unpriced_grades: The grades excluded for want of a price, ascending.
            Present so a caller can say which outcomes went unvalued rather than
            reporting a narrower expectation as a complete one.
        unpriced_probability: How much of the distribution they carried. The
            result does not retain the distribution, so this cannot be recovered
            afterwards, and it is the number #63's optimization strategies and
            #64's recommendation gate on.
    """

    amount: Money
    confidence: Confidence
    unpriced_grades: tuple[Grade, ...]
    unpriced_probability: float

    def __str__(self) -> str:
        missing = "" if not self.unpriced_grades else f", {len(self.unpriced_grades)} unpriced"
        return f"{self.amount} ({self.confidence}{missing})"


def _covers(bucket: Grade, point: Grade) -> bool:
    """Whether `point` is one of the grades `bucket` collapses.

    Only exact grades are ever covered. One bucket does not resolve another:
    ``6_or_lower`` is not a grade ``7_or_lower`` names, and reading a floor out
    of it would stack one estimate on top of another.
    """
    if point.is_bucket:
        return False
    if bucket.bound is GradeBound.OR_LOWER:
        return point.value <= bucket.value
    return point.value >= bucket.value


def _resolve(grade: Grade, prices: Mapping[Grade, GradedPrice]) -> GradedPrice | None:
    """The price for one term of a distribution, or `None` if there is none.

    An exact key always wins, buckets included: a caller who priced
    ``7_or_lower`` has answered the question and is not to be second-guessed.

    Failing that, a bucket takes **the lowest price among the grades it
    covers**. ``7_or_lower`` means "seven, or something worse", so its
    guaranteed value is the floor of what the ladder holds at or below seven —
    the boundary grade's own price would put the worst-case tail at its
    best-case member, which is optimistic in the direction that tilts a
    recommendation toward `grade`.

    ponytail: the floor is read off whichever grades the caller happened to
    price, so a ladder missing its cheap end reports a higher floor for the same
    bucket. That is the honest reading of what is known, and the cheap upgrade
    when it stops being good enough is for the caller to price the bucket key
    itself — which the exact-match branch above already honours — rather than
    for this function to learn the shape of a company's scale.
    """
    exact = prices.get(grade)
    if exact is not None:
        return exact
    if not grade.is_bucket:
        return None

    covered = [(point, price) for point, price in prices.items() if _covers(grade, point)]
    if not covered:
        return None
    # Ordered by grade as well as by amount, so two equally cheap grades resolve
    # the same way whatever order the caller's mapping happens to be in.
    return min(covered, key=lambda entry: (entry[1].value.amount, entry[0].sort_key))[1]


def expected_value(
    distribution: GradeDistribution,
    prices: Mapping[Grade, GradedPrice],
    *,
    distribution_confidence: Confidence,
) -> Uncertain[ExpectedValue]:
    """``Σ P(g)·V(g)`` over the grades that have a price (spec §40).

    Args:
        distribution: The retained grade distribution. Spec §63's validity rule
            is enforced by its constructor, so there is no check here and
            nothing invalid can be handed over.
        prices: What each grade is worth. Keys not in `distribution` are
            ignored, which lets a caller pass a whole 18- or 19-grade ladder.
        distribution_confidence: How far the distribution itself is trusted —
            `GradePrediction.model_confidence` from the grading company's model.
            Required, and deliberately without a default: assuming 1.0 for a
            model nobody measured is the same fabrication as pricing an unknown
            grade at zero, pointed the other way.

    Returns:
        An :class:`ExpectedValue`, or
        :class:`~tcg_domain.confidence.InsufficientInformation` with reason
        ``no_graded_price_available`` when no priced grade carries any
        probability. That is the only threshold here: "so little of the
        distribution is priced that the answer is not worth reporting" is a
        product judgement, and #64 owns it with the rest of
        `grade | do_not_grade | insufficient_information`.

    Raises:
        CurrencyMismatch: If the ladder mixes currencies. #53 owns the USD→SGD
            conversion; the economic engine takes SGD and never converts.

    The confidence is ``distribution_confidence · Σ_g P(g)·c(g)``, summed over
    the priced grades using the **original** probabilities. Weighting by
    probability is what makes a stale price on an unlikely grade cost little and
    one on the likely grade cost a lot, and leaving the unpriced grades in the
    sum as zero terms is what makes the renormalisation penalty fall out without
    a term of its own: with certain prices and a certain distribution the
    confidence *is* the priced mass.

    ponytail: that product is uncalibrated — three factors in ``[0, 1]``
    compound hard, and ``0.8 * 0.8 * 0.95`` reads as 0.61 to a user who would not
    call the inputs that weak. Calibration is M7/M8's problem and needs the
    Brier-score work CLAUDE.md names as a coverage gap; until then this is
    monotone in all three inputs, which is the property #64 needs.
    """
    weighted = Decimal(0)
    priced_mass = Decimal(0)
    confidence_terms: list[float] = []
    unpriced: list[Grade] = []
    currency: Currency | None = None

    for grade, probability in distribution.items():
        price = _resolve(grade, prices)
        if price is None:
            unpriced.append(grade)
            continue
        if currency is None:
            currency = price.value.currency
        elif price.value.currency != currency:
            raise CurrencyMismatch(
                f"the price ladder mixes {currency} with {price.value.currency}; convert first"
            )
        # `str` rather than `Decimal(probability)`: a probability arrives as a
        # binary float, and its shortest round-tripping form is the number the
        # model meant. The 55-digit binary expansion is not, and it would reach
        # the denominator as well as the terms.
        weight = Decimal(str(probability))
        weighted += weight * price.value.amount
        priced_mass += weight
        confidence_terms.append(probability * price.confidence.value)

    # Not "did any price resolve?": spec §63 permits `P(g) = 0`, so a ladder
    # that prices only a zero-probability grade resolves one and still leaves
    # nothing to divide by.
    if priced_mass == 0 or currency is None:
        return InsufficientInformation(NO_PRICE)

    return ExpectedValue(
        # Divided unconditionally rather than only when something is missing:
        # spec §63 accepts `Σ P(g)` within `SUM_TOLERANCE` of 1, so even a fully
        # priced distribution can be a fraction off. One `Money` at the end is
        # the single rounding.
        amount=Money(weighted / priced_mass, currency),
        # Clamped, because that same tolerance lets the weighted sum land just
        # above 1 and `Confidence` refuses anything that does.
        confidence=Confidence(
            min(1.0, distribution_confidence.value * math.fsum(confidence_terms))
        ),
        unpriced_grades=tuple(unpriced),
        unpriced_probability=math.fsum(distribution.probability_of(grade) for grade in unpriced),
    )
