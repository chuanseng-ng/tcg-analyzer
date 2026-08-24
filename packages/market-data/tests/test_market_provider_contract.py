"""One contract, run against the port's only implementation.

The acceptance criterion for #49 is "the port is defined with a working fake;
nothing in the core depends on a concrete provider". A fake asserted to be
equivalent to a port is worth very little; a suite that drives it **through a
`MarketDataProvider`-annotated binding** is the evidence itself, and is what
#52's adapter joins as a second parameter rather than getting a suite of its own
— the shape `packages/shared/tests/test_storage_contract.py` already uses.

Calling every method through that binding matters more than the annotation does.
CI type-checks `packages/*/src` only, so `mypy` never sees this file there; a
binding nobody calls would prove the port is satisfied on a developer's machine
and nowhere else. `@runtime_checkable` would not help — `isinstance` on a
Protocol checks method *names* and would pass on a class whose signatures are
all wrong.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from tcg_domain import (
    ENGLISH,
    JAPANESE,
    POKEMON,
    CardReference,
    Confidence,
    Grade,
    InsufficientInformation,
    Money,
)
from tcg_grading_companies import UnsupportedGrade
from tcg_market_data import (
    InMemoryMarketDataProvider,
    MarketDataProvider,
    MarketProviderUnavailable,
    PriceObservation,
)

CHARIZARD = CardReference(
    game=POKEMON,
    language=ENGLISH,
    set_code="base1",
    card_number="4/102",
    variant="unlimited-holo",
)
PIKACHU = CardReference(
    game=POKEMON,
    language=JAPANESE,
    set_code="SV2a",
    card_number="025/165",
    variant="normal",
)
NOON = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
SURE = Confidence.of(0.9)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    """Drive one async scenario.

    `asyncio.run` inside a sync test, as `test_storage_contract.py` does: this
    repository has no pytest-asyncio and adding one for a dozen tests would be a
    dependency in exchange for a decorator.
    """
    return asyncio.run(scenario())


def an_observation(
    card: CardReference = CHARIZARD,
    *,
    price: str = "420.00",
    at: datetime = NOON,
    company: str | None = None,
    grade: str | None = None,
) -> PriceObservation:
    return PriceObservation(
        card=card,
        price=Money.of(price),
        observed_at=at,
        confidence=SURE,
        provider="memory",
        grading_company=company,
        grade=None if grade is None else Grade.parse(grade),
    )


def a_provider(*observations: PriceObservation) -> InMemoryMarketDataProvider:
    return InMemoryMarketDataProvider(observations=list(observations))


class _UnreachableProvider:
    """A provider that is down, to prove failure is a third outcome.

    Not a mock library: three methods that raise is the whole of it, and the
    point is only that the port's contract — "implementations raise only
    `tcg_market_data.errors` types" — is satisfiable and catchable.
    """

    provider = "unreachable"

    async def get_raw_price(self, card: CardReference) -> PriceObservation:
        raise MarketProviderUnavailable(f"the provider did not answer for {card}")

    async def get_graded_price(
        self, card: CardReference, company: str, grade: Grade
    ) -> PriceObservation:
        raise MarketProviderUnavailable(f"the provider did not answer for {card}")

    async def get_price_history(self, card: CardReference) -> tuple[PriceObservation, ...]:
        raise MarketProviderUnavailable(f"the provider did not answer for {card}")


# --------------------------------------------------------------------------
# The port is satisfiable
# --------------------------------------------------------------------------
def test_the_fake_satisfies_the_port() -> None:
    """Structural, not nominal — an adapter never subclasses the Protocol."""
    provider: MarketDataProvider = a_provider(an_observation())

    assert provider.provider == "memory"
    assert run(lambda: provider.get_raw_price(CHARIZARD))
    assert run(lambda: provider.get_graded_price(CHARIZARD, "psa", Grade.parse("10"))) is not None
    assert run(lambda: provider.get_price_history(CHARIZARD))


def test_a_failing_provider_also_satisfies_the_port() -> None:
    provider: MarketDataProvider = _UnreachableProvider()
    with pytest.raises(MarketProviderUnavailable):
        run(lambda: provider.get_raw_price(CHARIZARD))


# --------------------------------------------------------------------------
# The three outcomes are three distinct things — the whole point of #49
# --------------------------------------------------------------------------
def test_a_zero_price_is_an_observation_not_an_absence() -> None:
    """The case a `Money.zero()` sentinel would destroy: a worthless card."""
    provider = a_provider(an_observation(price="0"))

    result = run(lambda: provider.get_raw_price(CHARIZARD))

    assert isinstance(result, PriceObservation)
    assert result.price == Money.zero()
    assert bool(result) is True


def test_an_unheld_price_is_unavailable_rather_than_an_error() -> None:
    provider = a_provider()

    result = run(lambda: provider.get_raw_price(CHARIZARD))

    assert isinstance(result, InsufficientInformation)
    assert not result
    assert result.reason is not None and str(CHARIZARD) in result.reason


def test_unavailability_is_a_result_and_can_never_be_raised() -> None:
    """Spec §2.7: "we cannot tell" is an answer, so it is not an Exception."""
    assert not issubclass(InsufficientInformation, Exception)


def test_a_provider_failure_is_distinct_from_both_and_is_a_connection_error() -> None:
    provider = _UnreachableProvider()

    with pytest.raises(MarketProviderUnavailable) as raised:
        run(lambda: provider.get_raw_price(CHARIZARD))

    assert isinstance(raised.value, ConnectionError)


# --------------------------------------------------------------------------
# Raw and graded do not answer each other's questions
# --------------------------------------------------------------------------
def test_a_graded_observation_does_not_answer_a_raw_question() -> None:
    """Otherwise a PSA 10 price is quietly reported as the card's raw value."""
    provider = a_provider(an_observation(company="psa", grade="10"))

    assert isinstance(run(lambda: provider.get_raw_price(CHARIZARD)), InsufficientInformation)


def test_a_raw_observation_does_not_answer_a_graded_question() -> None:
    provider = a_provider(an_observation())

    result = run(lambda: provider.get_graded_price(CHARIZARD, "psa", Grade.parse("10")))

    assert isinstance(result, InsufficientInformation)


def test_one_grade_does_not_answer_for_another() -> None:
    provider = a_provider(an_observation(company="psa", grade="10", price="1200.00"))

    nine = run(lambda: provider.get_graded_price(CHARIZARD, "psa", Grade.parse("9")))

    assert isinstance(nine, InsufficientInformation)


def test_one_card_does_not_answer_for_another() -> None:
    provider = a_provider(an_observation())

    assert isinstance(run(lambda: provider.get_raw_price(PIKACHU)), InsufficientInformation)


# --------------------------------------------------------------------------
# Grade keys are validated against the company's scale
# --------------------------------------------------------------------------
def test_a_grade_the_company_cannot_issue_is_refused_not_reported_unavailable() -> None:
    """PSA issues no 9.5. Asking for one is a caller bug, not a coverage gap."""
    provider = a_provider()

    with pytest.raises(UnsupportedGrade):
        run(lambda: provider.get_graded_price(CHARIZARD, "psa", Grade.parse("9.5")))


def test_the_same_grade_is_legal_for_the_company_that_issues_it() -> None:
    provider = a_provider(an_observation(company="bgs", grade="9.5", price="900.00"))

    result = run(lambda: provider.get_graded_price(CHARIZARD, "bgs", Grade.parse("9.5")))

    assert isinstance(result, PriceObservation)
    assert result.price == Money.of("900.00")


def test_a_company_with_no_adapter_is_unavailable_rather_than_refused() -> None:
    """Guards the open vocabulary: ADAPTERS is not the set of valid companies."""
    provider = a_provider()

    result = run(lambda: provider.get_graded_price(CHARIZARD, "cgc", Grade.parse("9.5")))

    assert isinstance(result, InsufficientInformation)


# --------------------------------------------------------------------------
# Which observation answers, and in what order history comes back
# --------------------------------------------------------------------------
def test_the_latest_observation_wins() -> None:
    provider = a_provider(
        an_observation(price="400.00", at=NOON - timedelta(days=1)),
        an_observation(price="420.00", at=NOON),
    )

    result = run(lambda: provider.get_raw_price(CHARIZARD))

    assert isinstance(result, PriceObservation)
    assert result.price == Money.of("420.00")


def test_the_last_of_two_simultaneous_observations_wins() -> None:
    """A stable sort, so appending overrides — `max` would keep the first."""
    provider = a_provider(
        an_observation(price="400.00"),
        an_observation(price="420.00"),
    )

    result = run(lambda: provider.get_raw_price(CHARIZARD))

    assert isinstance(result, PriceObservation)
    assert result.price == Money.of("420.00")


def test_history_comes_back_oldest_first() -> None:
    provider = a_provider(
        an_observation(price="420.00", at=NOON),
        an_observation(price="400.00", at=NOON - timedelta(days=2)),
        an_observation(price="410.00", at=NOON - timedelta(days=1)),
    )

    history = run(lambda: provider.get_price_history(CHARIZARD))

    assert [str(o.price.amount) for o in history] == ["400.00", "410.00", "420.00"]


def test_history_carries_raw_and_graded_together() -> None:
    provider = a_provider(
        an_observation(),
        an_observation(company="psa", grade="10", price="1200.00"),
    )

    history = run(lambda: provider.get_price_history(CHARIZARD))

    assert [o.grading_company for o in history] == [None, "psa"]


def test_simultaneous_observations_have_a_total_order() -> None:
    """#51 resolves a snapshot by re-reading; two readings must agree."""
    provider = a_provider(
        an_observation(company="psa", grade="10", price="1200.00"),
        an_observation(company="psa", grade="9", price="300.00"),
        an_observation(),
    )

    first = run(lambda: provider.get_price_history(CHARIZARD))
    provider.observations.reverse()
    second = run(lambda: provider.get_price_history(CHARIZARD))

    assert first == second
    assert [o.grade for o in first] == [None, Grade.parse("9"), Grade.parse("10")]


def test_a_card_with_no_history_gets_an_empty_tuple_not_an_absence() -> None:
    assert run(lambda: a_provider().get_price_history(CHARIZARD)) == ()


def test_history_holds_only_the_card_that_was_asked_for() -> None:
    provider = a_provider(an_observation(), an_observation(PIKACHU))

    history = run(lambda: provider.get_price_history(CHARIZARD))

    assert [o.card for o in history] == [CHARIZARD]
