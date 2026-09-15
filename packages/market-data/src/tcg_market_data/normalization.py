"""Spec §37's normalization and validation: a provider quote, made safe to store.

Issue #53 exists because a wildly implausible price is far more damaging than a
missing one. It flows straight into ``EV = Σ P(g)·V(g)`` and produces a
confident, wrong recommendation. So every quote either becomes an SGD
:class:`~tcg_market_data.port.PriceObservation` that records how it got there,
or a :class:`Quarantined` result naming why it did not. **Never an exception,
and never a silent drop**: an exception would abandon the rest of a run, and a
drop makes a coverage gap indistinguishable from a bug.

**A quote is deliberately loosely typed.** `PriceObservation` refuses an invalid
grade or a naive timestamp on construction, which is right for the port and
wrong here: a record that cannot be represented cannot be quarantined either. So
:class:`ProviderQuote` holds what the provider said, and :func:`normalize` is
where it is judged.

**The conversion is recorded, never implied.** A converted price without its
source currency, source amount and rate is a figure no historical snapshot can
reproduce, which is §36's whole purpose. :class:`NormalizedObservation` carries
all three. The rate is an operator-recorded ``exchange_rates`` row: no FX feed
exists, and one would be a new external provider needing its own ADR.
`tcg_domain.money.Currency` stays SGD-only, because the M4 binding keeps the
economic engine single-currency. The source amount is therefore a `Decimal` and
a code, never `Money`.

**Nothing is smoothed, averaged or interpolated.** A quote is a raw fact, and
pre-smoothing hides exactly the volatility that should reduce confidence.

This module is pure: the caller resolves the provider identifier to cards and
chooses the rate, both of which need a database. `tcg_api.market.normalization`
is that caller.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, DecimalException
from enum import StrEnum
from typing import Final
from uuid import UUID

from tcg_domain.card import CardReference
from tcg_domain.confidence import Confidence
from tcg_domain.errors import InvalidConfidence, InvalidGrade
from tcg_domain.grade import Grade
from tcg_domain.money import Money
from tcg_grading_companies import GradingCompany
from tcg_grading_companies.errors import UnsupportedGrade

from tcg_market_data.errors import InvalidMarketObservation
from tcg_market_data.port import PriceObservation, validated_grade_key

__all__ = [
    "MAX_CLOCK_SKEW",
    "MAX_OBSERVATION_AGE",
    "MAX_PRICE_SGD",
    "MAX_RATE_AGE",
    "ExchangeRate",
    "NormalizedObservation",
    "ProviderQuote",
    "QuarantineReason",
    "Quarantined",
    "normalize",
]

#: The highest SGD price stored. Above it, a quote is quarantined.
#:
#: ponytail: uncalibrated, because nothing has been ingested to calibrate it
#: against. It catches a decimal-point slip or a price in the wrong unit, and
#: cannot catch a plausible but wrong tenfold jump. #54's first real runs
#: recalibrate it, and a jump-against-last-price check is the upgrade once there
#: is a history to measure the factor from.
MAX_PRICE_SGD: Final = Money.of("500000")

#: How far in the past a quote's `observed_at` may lie. Anything older is not
#: today's market, and a backfill is a deliberate act that should not arrive
#: through the daily path. ponytail: uncalibrated, #54 recalibrates.
MAX_OBSERVATION_AGE: Final = timedelta(days=30)

#: How far in the future `observed_at` may lie: clock skew, not prophecy.
MAX_CLOCK_SKEW: Final = timedelta(minutes=5)

#: How old a rate may be relative to the day it converts. A rate recorded
#: weekly still serves; one from last quarter would misprice every quote.
#: ponytail: uncalibrated, #54 recalibrates.
MAX_RATE_AGE: Final = timedelta(days=7)

#: The currency every stored price is normalized to (spec §46: all values SGD).
_SGD: Final = "SGD"

_CURRENCY: Final = re.compile(r"^[A-Z]{3}$")

#: The source column is NUMERIC(12, 2), so a third decimal place is refused
#: rather than rounded into something the provider never said.
_CENTS_EXPONENT: Final = -2


class QuarantineReason(StrEnum):
    """Why a quote was not stored. Closed: `market_quarantine`'s CHECK is built from it.

    `UNMAPPED_CARD` is a catalog problem worth reporting, not something to
    invent a card for. `AMBIGUOUS_CARD` exists because
    `ix_card_external_ids_provider_external_id` is deliberately non-unique: one
    provider identifier can name a holo and a reverse holo both, and choosing one
    would be a guess stored as a fact.
    """

    UNMAPPED_CARD = "unmapped_card"
    AMBIGUOUS_CARD = "ambiguous_card"
    MISSING_FIELD = "missing_field"
    INVALID_PRICE = "invalid_price"
    PRICE_OUT_OF_BOUNDS = "price_out_of_bounds"
    INVALID_CONFIDENCE = "invalid_confidence"
    INVALID_CURRENCY = "invalid_currency"
    NO_EXCHANGE_RATE = "no_exchange_rate"
    UNSUPPORTED_COMPANY = "unsupported_company"
    INVALID_GRADE = "invalid_grade"
    UNSUPPORTED_GRADE = "unsupported_grade"
    IMPLAUSIBLE_OBSERVED_AT = "implausible_observed_at"


@dataclass(frozen=True, slots=True)
class ProviderQuote:
    """One price, as a provider said it, before anything has judged it.

    #52's adapter emits these. Every field but `external_id` may be absent or
    malformed, because that is what a bad upstream day looks like and
    :func:`normalize` must be able to say so.

    Args:
        external_id: The provider's own identifier for the card, verbatim — what
            `card_external_ids.external_id` stores.
        amount: The price as quoted: a decimal string or a `Decimal`.
        currency: The ISO 4217 code quoted in.
        observed_at: When the provider saw the price. Must be timezone-aware.
        confidence: The provider's own signal, in [0, 1].
        grading_company: A company slug for a graded price, `None` for raw.
        grade: A grade key (``"9.5"``, ``"7_or_lower"``) for a graded price.
        metadata: Whatever else the provider reported. Stored beside the price.
    """

    external_id: str
    amount: str | Decimal | None
    currency: str | None
    observed_at: datetime | None
    confidence: float | None
    grading_company: str | None = None
    grade: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def as_record(self) -> dict[str, object]:
        """The quote as strict JSON values, for `market_quarantine.record`.

        Text for a `Decimal` and ISO 8601 for a timestamp, so nothing is rounded
        or re-zoned on the way into JSONB: a quarantined record has to show what
        the provider said, not what a serializer made of it. A non-finite number
        becomes text too, nested values included — JSONB refuses NaN, and one
        such value would fail the INSERT for every good quote in the batch.
        """

        def text(value: object) -> object:
            if value is None or isinstance(value, bool | int | str):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, Mapping):
                return {str(key): text(item) for key, item in value.items()}
            if isinstance(value, list | tuple):
                return [text(item) for item in value]
            return str(value)

        return {
            "external_id": text(self.external_id),
            "amount": text(self.amount),
            "currency": text(self.currency),
            "observed_at": text(self.observed_at),
            "confidence": text(self.confidence),
            "grading_company": text(self.grading_company),
            "grade": text(self.grade),
            "metadata": text(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """One operator-recorded rate: 1 `base_currency` = `rate` SGD, as of a day.

    Args:
        id: The `exchange_rates` row, which every converted observation names.
        base_currency: The ISO 4217 code converted from.
        rate: SGD per unit of `base_currency`, exactly.
        as_of: The day the rate applies to.
    """

    id: UUID
    base_currency: str
    rate: Decimal
    as_of: date


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    """A quote that passed, and the three facts that reproduce its SGD price.

    Args:
        observation: The SGD observation.
        source_price: The amount as quoted, exactly.
        source_currency: The code it was quoted in.
        exchange_rate: The rate that converted it; `None` only for an SGD quote.
    """

    observation: PriceObservation
    source_price: Decimal
    source_currency: str
    exchange_rate: ExchangeRate | None


@dataclass(frozen=True, slots=True)
class Quarantined:
    """A quote that did not pass, and why. Always stored, never dropped."""

    reason: QuarantineReason
    detail: str


def _amount(value: object) -> Decimal | None:
    """A finite, non-negative amount in whole cents, or `None`."""
    if isinstance(value, float | bool) or not isinstance(value, str | Decimal):
        return None
    try:
        amount = Decimal(value)
    except (DecimalException, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    exponent = amount.as_tuple().exponent
    if (
        isinstance(exponent, int)
        and exponent < _CENTS_EXPONENT
        and amount != amount.quantize(Decimal("0.01"))
    ):
        return None
    return amount


def normalize(
    quote: ProviderQuote,
    *,
    provider: str,
    cards: Sequence[CardReference],
    rate: ExchangeRate | None,
    now: datetime,
) -> NormalizedObservation | Quarantined:
    """Judge one quote: store it in SGD, or quarantine it with a reason.

    Args:
        quote: What the provider said.
        provider: The provider's slug, stamped on the observation.
        cards: Every card the quote's `external_id` resolves to. Exactly one is
            required; none is `unmapped_card` and several is `ambiguous_card`.
        rate: The rate the caller chose for this quote's currency and day, or
            `None` if there is none. Checked here rather than trusted.
        now: The moment of normalizing, timezone-aware — what `observed_at` is
            judged against.

    Returns:
        A :class:`NormalizedObservation`, or a :class:`Quarantined` naming the
        first check the quote failed. Never raises for bad data.
    """
    if not cards:
        return Quarantined(
            QuarantineReason.UNMAPPED_CARD,
            f"no catalog card carries {provider} identifier {quote.external_id!r}",
        )
    if len(cards) > 1:
        return Quarantined(
            QuarantineReason.AMBIGUOUS_CARD,
            f"{provider} identifier {quote.external_id!r} names {len(cards)} catalog cards",
        )

    missing = [
        name
        for name in ("amount", "currency", "observed_at", "confidence")
        if getattr(quote, name) is None
    ]
    if (quote.grading_company is None) != (quote.grade is None):
        missing.append("grade" if quote.grade is None else "grading_company")
    if missing:
        return Quarantined(QuarantineReason.MISSING_FIELD, f"missing {', '.join(missing)}")

    currency = quote.currency
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        return Quarantined(
            QuarantineReason.INVALID_CURRENCY, f"{currency!r} is not an ISO 4217 code"
        )

    source_price = _amount(quote.amount)
    if source_price is None:
        return Quarantined(
            QuarantineReason.INVALID_PRICE,
            f"{quote.amount!r} is not a non-negative decimal amount in cents",
        )

    try:
        confidence = Confidence.of(quote.confidence)  # type: ignore[arg-type]
    except InvalidConfidence as error:
        return Quarantined(QuarantineReason.INVALID_CONFIDENCE, str(error))

    grade: Grade | None = None
    company = quote.grading_company
    if company is not None:
        if company not in {member.value for member in GradingCompany}:
            return Quarantined(
                QuarantineReason.UNSUPPORTED_COMPANY, f"{company!r} is not a V1 grading company"
            )
        try:
            grade = Grade.parse(quote.grade)  # type: ignore[arg-type]
        except InvalidGrade as error:
            return Quarantined(QuarantineReason.INVALID_GRADE, str(error))
        # `Grade.parse` canonicalises `9.0` to `9`; the stored key must be what
        # the provider sent, so a non-canonical spelling is refused, not rewritten.
        if str(grade) != quote.grade:
            return Quarantined(
                QuarantineReason.INVALID_GRADE, f"{quote.grade!r} is not a canonical grade key"
            )
        try:
            validated_grade_key(company, grade)
        except UnsupportedGrade as error:
            return Quarantined(QuarantineReason.UNSUPPORTED_GRADE, str(error))

    observed_at = quote.observed_at
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        return Quarantined(
            QuarantineReason.IMPLAUSIBLE_OBSERVED_AT,
            f"{observed_at!r} is not a timezone-aware timestamp",
        )
    if not now - MAX_OBSERVATION_AGE <= observed_at <= now + MAX_CLOCK_SKEW:
        return Quarantined(
            QuarantineReason.IMPLAUSIBLE_OBSERVED_AT,
            f"{observed_at.isoformat()} is outside [{MAX_OBSERVATION_AGE} ago, "
            f"{MAX_CLOCK_SKEW} ahead] of {now.isoformat()}",
        )

    used_rate: ExchangeRate | None = None
    if currency == _SGD:
        sgd = source_price
    else:
        observed_on = observed_at.date()
        if (
            rate is None
            or rate.base_currency != currency
            or not observed_on - MAX_RATE_AGE <= rate.as_of <= observed_on
        ):
            return Quarantined(
                QuarantineReason.NO_EXCHANGE_RATE,
                f"no {currency}/SGD rate recorded within {MAX_RATE_AGE.days} days "
                f"before {observed_on.isoformat()}",
            )
        sgd = source_price * rate.rate
        used_rate = rate

    price = Money.of(sgd)
    if price > MAX_PRICE_SGD:
        return Quarantined(
            QuarantineReason.PRICE_OUT_OF_BOUNDS, f"{price} exceeds the bound of {MAX_PRICE_SGD}"
        )

    try:
        observation = PriceObservation(
            card=cards[0],
            price=price,
            observed_at=observed_at,
            confidence=confidence,
            provider=provider,
            grading_company=company,
            grade=grade,
        )
    except InvalidMarketObservation as error:  # pragma: no cover - every rule is checked above
        return Quarantined(QuarantineReason.MISSING_FIELD, str(error))

    return NormalizedObservation(
        observation=observation,
        source_price=source_price,
        source_currency=currency,
        exchange_rate=used_rate,
    )
