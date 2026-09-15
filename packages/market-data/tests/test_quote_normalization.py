"""What normalization stores, and what it quarantines instead.

Issue #53's rule, stated once: a bad upstream day must never poison
``EV = Σ P(g)·V(g)``. So a quote either becomes an SGD `PriceObservation` that
records how it got there, or a `Quarantined` result naming why it did not —
never an exception, and never a silent drop. A wildly implausible price is
worse than a missing one, because a missing one is at least visibly missing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from tcg_domain import ENGLISH, POKEMON, CardReference, Grade, Money
from tcg_market_data import (
    MAX_PRICE_SGD,
    ExchangeRate,
    NormalizedObservation,
    ProviderQuote,
    Quarantined,
    QuarantineReason,
    normalize,
)

CHARIZARD = CardReference(
    game=POKEMON,
    language=ENGLISH,
    set_code="base1",
    card_number="4/102",
    variant="unlimited-holo",
)
CHARIZARD_REVERSE = CardReference(
    game=POKEMON,
    language=ENGLISH,
    set_code="base1",
    card_number="4/102",
    variant="reverse-holo",
)
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
USD_SGD = ExchangeRate(
    id=uuid4(), base_currency="USD", rate=Decimal("1.3421"), as_of=date(2026, 9, 15)
)


def a_quote(**overrides: object) -> ProviderQuote:
    fields: dict[str, object] = {
        "external_id": "base1-4",
        "amount": "100.00",
        "currency": "USD",
        "observed_at": NOW - timedelta(hours=1),
        "confidence": 0.8,
    }
    fields.update(overrides)
    return ProviderQuote(**fields)  # type: ignore[arg-type]


def run(
    quote: ProviderQuote,
    *,
    cards: tuple[CardReference, ...] = (CHARIZARD,),
    rate: ExchangeRate | None = USD_SGD,
) -> NormalizedObservation | Quarantined:
    return normalize(quote, provider="memory", cards=cards, rate=rate, now=NOW)


def quarantined(result: NormalizedObservation | Quarantined) -> QuarantineReason:
    assert isinstance(result, Quarantined), f"expected a quarantine, got {result}"
    assert result.detail, "a quarantine must say why"
    return result.reason


def stored(result: NormalizedObservation | Quarantined) -> NormalizedObservation:
    assert isinstance(result, NormalizedObservation), f"expected a stored price, got {result}"
    return result


# --------------------------------------------------------------------------
# Well-formed quotes normalize
# --------------------------------------------------------------------------
def test_a_raw_usd_quote_becomes_an_sgd_observation() -> None:
    """USD 100.00 x 1.3421 = SGD 134.21, by hand."""
    result = stored(run(a_quote()))

    assert result.observation.price == Money.of("134.21")
    assert result.observation.card == CHARIZARD
    assert result.observation.provider == "memory"
    assert result.observation.grading_company is None
    assert result.observation.confidence.value == 0.8


def test_a_graded_quote_keeps_its_company_and_grade() -> None:
    result = stored(run(a_quote(grading_company="bgs", grade="9.5")))

    assert result.observation.grading_company == "bgs"
    assert result.observation.grade == Grade.parse("9.5")


def test_conversion_records_the_source_currency_price_and_rate() -> None:
    """Without all three a historical snapshot cannot be reproduced (§36)."""
    result = stored(run(a_quote()))

    assert result.source_currency == "USD"
    assert result.source_price == Decimal("100.00")
    assert result.exchange_rate == USD_SGD


def test_conversion_rounds_half_away_from_zero_like_money() -> None:
    """USD 1.00 x 1.345 = 1.345, which is SGD 1.35 and never 1.34."""
    rate = ExchangeRate(id=uuid4(), base_currency="USD", rate=Decimal("1.345"), as_of=NOW.date())
    result = stored(run(a_quote(amount="1.00"), rate=rate))

    assert result.observation.price == Money.of("1.35")


def test_an_sgd_quote_needs_no_rate_and_records_none() -> None:
    result = stored(run(a_quote(currency="SGD", amount="42.00"), rate=None))

    assert result.observation.price == Money.of("42.00")
    assert result.exchange_rate is None
    assert result.source_currency == "SGD"


def test_a_zero_price_is_stored_not_quarantined() -> None:
    """Zero is a measurement about a worthless card, not a missing price."""
    result = stored(run(a_quote(amount="0")))

    assert result.observation.price == Money.zero()


def test_a_decimal_amount_is_accepted_as_well_as_a_string() -> None:
    assert stored(run(a_quote(amount=Decimal("100.00")))).observation.price == Money.of("134.21")


# --------------------------------------------------------------------------
# Card mapping
# --------------------------------------------------------------------------
def test_an_unmapped_identifier_is_quarantined_not_dropped() -> None:
    """A catalog problem worth reporting — never a card invented for it."""
    assert quarantined(run(a_quote(), cards=())) is QuarantineReason.UNMAPPED_CARD


def test_an_identifier_naming_two_cards_is_quarantined_not_guessed() -> None:
    """`card_external_ids` is deliberately non-unique; picking one would be a guess."""
    result = run(a_quote(), cards=(CHARIZARD, CHARIZARD_REVERSE))

    assert quarantined(result) is QuarantineReason.AMBIGUOUS_CARD


# --------------------------------------------------------------------------
# Required fields and prices
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field", ["amount", "currency", "observed_at", "confidence"])
def test_a_missing_required_field_is_quarantined(field: str) -> None:
    assert quarantined(run(a_quote(**{field: None}))) is QuarantineReason.MISSING_FIELD


@pytest.mark.parametrize(
    "overrides",
    [{"grading_company": "psa"}, {"grade": "10"}],
)
def test_a_half_graded_quote_is_quarantined(overrides: dict[str, object]) -> None:
    assert quarantined(run(a_quote(**overrides))) is QuarantineReason.MISSING_FIELD


@pytest.mark.parametrize("amount", ["abc", "NaN", "Infinity", "-1.00", "1.001", 1.5])
def test_an_unreadable_price_is_quarantined(amount: object) -> None:
    """A float, a third decimal place and a negative are all not a price we store.

    A third place is refused rather than rounded: the source column holds cents,
    and rounding would store something the provider never said.
    """
    assert quarantined(run(a_quote(amount=amount))) is QuarantineReason.INVALID_PRICE


def test_a_price_above_the_bound_is_quarantined_not_stored() -> None:
    """The damage a confident wrong price does in `EV` is the reason for this."""
    over = (MAX_PRICE_SGD.amount + Decimal("0.01")).to_eng_string()
    result = run(a_quote(currency="SGD", amount=over), rate=None)

    assert quarantined(result) is QuarantineReason.PRICE_OUT_OF_BOUNDS


def test_the_bound_applies_after_conversion() -> None:
    """USD 400,000 is under 500,000 in the currency quoted and over it in SGD."""
    assert quarantined(run(a_quote(amount="400000.00"))) is QuarantineReason.PRICE_OUT_OF_BOUNDS


def test_a_price_at_the_bound_is_stored() -> None:
    result = run(a_quote(currency="SGD", amount=str(MAX_PRICE_SGD.amount)), rate=None)

    assert stored(result).observation.price == MAX_PRICE_SGD


@pytest.mark.parametrize("confidence", [1.5, -0.1, float("nan")])
def test_an_out_of_range_confidence_is_quarantined(confidence: float) -> None:
    assert quarantined(run(a_quote(confidence=confidence))) is QuarantineReason.INVALID_CONFIDENCE


# --------------------------------------------------------------------------
# Currency and rates
# --------------------------------------------------------------------------
@pytest.mark.parametrize("currency", ["usd", "US", "DOLLARS"])
def test_a_currency_that_is_not_an_iso_code_is_quarantined(currency: str) -> None:
    assert quarantined(run(a_quote(currency=currency))) is QuarantineReason.INVALID_CURRENCY


def test_a_foreign_quote_without_a_rate_is_quarantined() -> None:
    assert quarantined(run(a_quote(), rate=None)) is QuarantineReason.NO_EXCHANGE_RATE


def test_a_rate_for_another_currency_is_not_used() -> None:
    eur = ExchangeRate(id=uuid4(), base_currency="EUR", rate=Decimal("1.5"), as_of=NOW.date())

    assert quarantined(run(a_quote(), rate=eur)) is QuarantineReason.NO_EXCHANGE_RATE


def test_a_stale_rate_is_not_used() -> None:
    stale = ExchangeRate(
        id=uuid4(),
        base_currency="USD",
        rate=Decimal("1.3"),
        as_of=NOW.date() - timedelta(days=8),
    )

    assert quarantined(run(a_quote(), rate=stale)) is QuarantineReason.NO_EXCHANGE_RATE


def test_a_rate_dated_after_the_observation_is_not_used() -> None:
    later = ExchangeRate(
        id=uuid4(),
        base_currency="USD",
        rate=Decimal("1.3"),
        as_of=NOW.date() + timedelta(days=1),
    )

    assert quarantined(run(a_quote(), rate=later)) is QuarantineReason.NO_EXCHANGE_RATE


# --------------------------------------------------------------------------
# Grades
# --------------------------------------------------------------------------
def test_a_grade_the_company_does_not_issue_is_quarantined() -> None:
    """PSA issues no 9.5; BGS does."""
    result = run(a_quote(grading_company="psa", grade="9.5"))

    assert quarantined(result) is QuarantineReason.UNSUPPORTED_GRADE


@pytest.mark.parametrize("grade", ["9.25", "11", "ten", "9.0"])
def test_a_grade_key_that_is_not_a_grade_is_quarantined(grade: str) -> None:
    result = run(a_quote(grading_company="psa", grade=grade))

    assert quarantined(result) is QuarantineReason.INVALID_GRADE


@pytest.mark.parametrize("company", ["cgc", "PSA"])
def test_a_company_v1_does_not_ship_is_quarantined(company: str) -> None:
    """The database refuses the row, so one such quote would sink a whole batch."""
    result = run(a_quote(grading_company=company, grade="10"))

    assert quarantined(result) is QuarantineReason.UNSUPPORTED_COMPANY


# --------------------------------------------------------------------------
# Observed-at
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "observed_at",
    [
        NOW + timedelta(minutes=6),
        NOW - timedelta(days=31),
        datetime(2026, 9, 15, 11, 0),  # noqa: DTZ001 - naive is the point
    ],
)
def test_an_implausible_observation_time_is_quarantined(observed_at: datetime) -> None:
    result = run(a_quote(observed_at=observed_at))

    assert quarantined(result) is QuarantineReason.IMPLAUSIBLE_OBSERVED_AT


def test_a_little_clock_skew_is_tolerated() -> None:
    assert stored(run(a_quote(observed_at=NOW + timedelta(minutes=4))))


# --------------------------------------------------------------------------
# The quarantine record
# --------------------------------------------------------------------------
def test_a_quote_renders_as_a_json_safe_record_of_what_was_said() -> None:
    """What `market_quarantine.record` stores: the quote verbatim, as text."""
    import json

    quote = a_quote(amount=Decimal("1.50"), grading_company="psa", grade="10", metadata={"n": 3})
    record = quote.as_record()

    assert json.loads(json.dumps(record)) == record
    assert record["amount"] == "1.50"
    assert record["observed_at"] == (NOW - timedelta(hours=1)).isoformat()
    assert record["metadata"] == {"n": 3}


def test_a_record_is_strict_json_even_when_the_provider_sent_none() -> None:
    """JSONB refuses NaN, and a Decimal is not JSON at all.

    One such value would fail the batch's INSERT and abandon every good quote
    beside it, so non-finite numbers and anything not JSON become text — nested
    inside `metadata` too, because stored observations write it as well.
    """
    import json

    quote = a_quote(
        confidence=float("nan"),
        metadata={"spread": float("inf"), "median": Decimal("1.5"), "sales": [1, float("nan")]},
    )
    record = quote.as_record()

    json.dumps(record, allow_nan=False)
    assert record["confidence"] == "nan"
    assert record["metadata"] == {"spread": "inf", "median": "1.5", "sales": [1, "nan"]}


def test_normalize_never_raises_for_bad_data() -> None:
    """Every refusal is a result; an exception would abandon the whole run."""
    garbage = ProviderQuote(
        external_id="x",
        amount=object(),  # type: ignore[arg-type]
        currency=7,  # type: ignore[arg-type]
        observed_at="yesterday",  # type: ignore[arg-type]
        confidence="high",  # type: ignore[arg-type]
        grading_company=3,  # type: ignore[arg-type]
        grade=[],  # type: ignore[arg-type]
    )

    assert isinstance(run(garbage), Quarantined)
