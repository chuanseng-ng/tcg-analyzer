"""Spec §40's `EV = Σ P(g)·V(g)`, and what it does when a price is missing.

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance
criterion is that the economics are "independently unit-tested against manually
calculated fixtures", so a number that came out of the implementation is not
evidence — the ones below were computed on paper, and they are written before
the module they check.

Two of these are the acceptance criterion itself:
`test_an_unpriced_grade_is_never_valued_at_zero` fails if somebody "simplifies"
a missing price to zero, and
`test_expected_value_equals_the_common_price_when_every_grade_is_worth_the_same`
fails if somebody quantises each term instead of the sum.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from tcg_domain import (
    Confidence,
    CurrencyMismatch,
    Grade,
    GradeDistribution,
    InsufficientInformation,
    InvalidGradeDistribution,
    Money,
)
from tcg_economic_engine import (
    ExpectedValue,
    GradedPrice,
    InvalidGradedPrice,
    SellingFee,
    expected_value,
)

CERTAIN = Confidence.of(1.0)

# A realistic PSA-shaped output, taken from spec §24 — bucketed tail included.
SPEC_EXAMPLE = {"10": 0.12, "9": 0.69, "8": 0.17, "7_or_lower": 0.02}


def distribution(mapping: dict[str, float]) -> GradeDistribution:
    return GradeDistribution.from_mapping(mapping)


def prices(mapping: dict[str, str], confidence: Confidence = CERTAIN) -> dict[Grade, GradedPrice]:
    """A price ladder from grade keys to decimal strings, all equally trusted."""
    return {
        Grade.parse(key): GradedPrice(Money.of(amount), confidence)
        for key, amount in mapping.items()
    }


def answer(result: object) -> ExpectedValue:
    """Assert the result is a figure rather than an admission, and narrow it."""
    assert isinstance(result, ExpectedValue), result
    return result


# --------------------------------------------------------------------------
# The sum itself
# --------------------------------------------------------------------------


def test_a_three_grade_expectation_matches_the_hand_calculation() -> None:
    """0.10·1000 + 0.60·400 + 0.30·150 = 100.00 + 240.00 + 45.00."""
    result = answer(
        expected_value(
            distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
            prices({"10": "1000.00", "9": "400.00", "8": "150.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("385.00")
    assert result.unpriced_grades == ()
    assert result.unpriced_probability == pytest.approx(0.0)


def test_adr_0007_example_one_before_any_selling_fee() -> None:
    """0.5·200 + 0.5·320 = 260.00 — ADR 0007's `graded_proceeds`, fee-free.

    #60 and #62 consume this number verbatim, so it is pinned here too.
    """
    result = answer(
        expected_value(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("260.00")


def test_bgs_half_grades_compute_correctly() -> None:
    """Spec §24 requires BGS half grades: 0.05·2000 + 0.25·600 + 0.45·250 + 0.25·120."""
    result = answer(
        expected_value(
            distribution({"10": 0.05, "9.5": 0.25, "9": 0.45, "8.5": 0.25}),
            prices({"10": "2000.00", "9.5": "600.00", "9": "250.00", "8.5": "120.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("392.50")


def test_expected_value_equals_the_common_price_when_every_grade_is_worth_the_same() -> None:
    """The whole distribution is worth 100.04, so the expectation is 100.04.

    Quantising each term instead of the sum answers 100.05: 0.125·100.04 is
    12.505, which rounds to 12.51, and 0.875·100.04 is 87.535, which rounds to
    87.54. The exact products sum to 100.04 with nothing left to round. This is
    why the terms are `Decimal` and `Money` is built once, at the end.
    """
    result = answer(
        expected_value(
            distribution({"10": 0.125, "9": 0.875}),
            prices({"10": "100.04", "9": "100.04"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("100.04")


def test_a_probability_is_read_as_written_not_as_a_binary_float() -> None:
    """A tenth of ten cents is one cent, not 0.010000000000000002."""
    result = answer(
        expected_value(
            distribution({"10": 0.1, "9": 0.9}),
            prices({"10": "0.10", "9": "0.10"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("0.10")


def test_the_fee_can_be_netted_per_outcome_before_the_sum() -> None:
    """ADR 0007 example 2, the way #60 reaches it: 0.5·180 + 0.5·288 = 234.00.

    The ADR applies `sale_costs` "per outcome, inside the sum, never to the
    expected value", and forecloses computing EV once and netting fees
    afterwards. A caller satisfies that literally by netting each `V(g)` before
    handing the ladder over — which is why this function grows no fee parameter
    and cost subtraction stays #60's job.
    """
    fee = SellingFee(rate=Decimal("0.10"))
    gross = prices({"9": "200.00", "10": "320.00"})
    net = {
        grade: GradedPrice(price.value - fee.on(price.value), price.confidence)
        for grade, price in gross.items()
    }

    result = answer(
        expected_value(
            distribution({"9": 0.5, "10": 0.5}),
            net,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("234.00")


# --------------------------------------------------------------------------
# A missing V(g) — the acceptance criterion
# --------------------------------------------------------------------------


def test_an_unpriced_grade_is_excluded_and_the_rest_renormalised() -> None:
    """(0.60·400 + 0.30·150) / 0.90 = 285.00 / 0.90 = 316.67."""
    result = answer(
        expected_value(
            distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
            prices({"9": "400.00", "8": "150.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("316.67")
    assert result.unpriced_grades == (Grade.parse("10"),)
    assert result.unpriced_probability == pytest.approx(0.10)


def test_an_unpriced_grade_is_never_valued_at_zero() -> None:
    """Spec §69/M5 acceptance: "no unpriced grade is ever valued at zero".

    Treating the missing PSA 10 as worth nothing answers 285.00 and drags the
    expectation below every price in the ladder — enough to flip a
    recommendation from `grade` to `do_not_grade` on a card whose only unknown
    is its best outcome.
    """
    result = answer(
        expected_value(
            distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
            prices({"9": "400.00", "8": "150.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount > Money.of("285.00")


def test_no_price_at_all_is_insufficient_information() -> None:
    """Spec §2.7: "we cannot tell" is a result, never a fabricated number."""
    result = expected_value(
        distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
        {},
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)
    assert result.reason == "no_graded_price_available"
    assert not result


def test_prices_for_other_grades_are_not_prices_for_these() -> None:
    """A ladder for grades nobody predicted answers nothing about this card."""
    result = expected_value(
        distribution({"10": 0.4, "9": 0.6}),
        prices({"6": "40.00", "5": "30.00"}),
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)


def test_a_priced_grade_of_zero_probability_is_not_a_priced_distribution() -> None:
    """Spec §63 permits `P(g) = 0`, so a price on one is no coverage at all.

    Guarding on "did any price resolve?" instead of "how much mass did?" makes
    this a division by zero rather than an honest admission.
    """
    result = expected_value(
        distribution({"10": 0.0, "9": 1.0}),
        prices({"10": "1000.00"}),
        distribution_confidence=CERTAIN,
    )

    assert isinstance(result, InsufficientInformation)


def test_a_zero_price_is_a_price_and_not_a_missing_one() -> None:
    """0.5·100.00 + 0.5·0.00 = 50.00. `0.00` is a real price; `null` is not.

    The same distinction `GET /cards/{id}/market` keeps on the wire, where a
    price the snapshot does not hold is `null` and never `0.00`.
    """
    result = answer(
        expected_value(
            distribution({"9": 0.5, "8": 0.5}),
            prices({"9": "100.00", "8": "0.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("50.00")
    assert result.unpriced_grades == ()


def test_unpriced_grades_are_reported_in_ascending_order() -> None:
    """Falls out of `GradeDistribution`'s sorted iteration; asserted so it stays."""
    result = answer(
        expected_value(
            distribution({"10": 0.2, "9": 0.5, "8": 0.2, "7": 0.1}),
            prices({"9": "400.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.unpriced_grades == (Grade.parse("7"), Grade.parse("8"), Grade.parse("10"))
    assert result.unpriced_probability == pytest.approx(0.5)


def test_prices_for_grades_outside_the_distribution_are_ignored() -> None:
    """A caller hands over the whole 18- or 19-grade ladder; most of it is unused."""
    ladder = prices({"10": "1000.00", "9": "400.00", "8": "150.00", "7": "90.00", "6": "60.00"})

    result = answer(
        expected_value(
            distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
            ladder,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("385.00")


# --------------------------------------------------------------------------
# Bucketed tails — spec §24's `7_or_lower`
# --------------------------------------------------------------------------


def test_a_bucket_is_valued_at_the_lowest_price_it_covers() -> None:
    """`7_or_lower` covers 7, 6.5 and 6; the lowest of those is 45.00.

    0.12·1200 + 0.69·700 + 0.17·300 + 0.02·45
      = 144.00 + 483.00 + 51.00 + 0.90 = 678.90.
    """
    result = answer(
        expected_value(
            distribution(SPEC_EXAMPLE),
            prices(
                {
                    "10": "1200.00",
                    "9": "700.00",
                    "8": "300.00",
                    "7": "60.00",
                    "6.5": "50.00",
                    "6": "45.00",
                }
            ),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("678.90")
    assert result.unpriced_grades == ()


def test_a_bucket_never_takes_the_price_of_its_boundary_grade() -> None:
    """Valuing `7_or_lower` at V(7) prices the worst-case tail at its best member.

    That answers 679.20 — above the bound the bucket actually guarantees, and
    above it in the direction that tilts a recommendation toward `grade`.
    """
    result = answer(
        expected_value(
            distribution(SPEC_EXAMPLE),
            prices(
                {
                    "10": "1200.00",
                    "9": "700.00",
                    "8": "300.00",
                    "7": "60.00",
                    "6.5": "50.00",
                    "6": "45.00",
                }
            ),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount < Money.of("679.20")


def test_an_or_higher_bucket_is_valued_at_the_lowest_price_it_covers() -> None:
    """`9_or_higher` covers 9, 9.5 and 10; the lowest is 500.00.

    0.4·500 + 0.6·100 = 200.00 + 60.00 = 260.00.
    """
    result = answer(
        expected_value(
            distribution({"9_or_higher": 0.4, "8": 0.6}),
            prices({"8": "100.00", "9": "500.00", "9.5": "600.00", "10": "900.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("260.00")


def test_a_price_under_the_buckets_own_key_wins() -> None:
    """An explicit `7_or_lower` price is the caller's answer, not one to derive.

    0.12·1200 + 0.69·700 + 0.17·300 + 0.02·60 = 679.20, and the 10.00 on grade
    6 is never consulted.
    """
    result = answer(
        expected_value(
            distribution(SPEC_EXAMPLE),
            prices(
                {
                    "10": "1200.00",
                    "9": "700.00",
                    "8": "300.00",
                    "7_or_lower": "60.00",
                    "6": "10.00",
                }
            ),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("679.20")


def test_a_bucket_covering_nothing_priced_is_excluded_like_any_other_grade() -> None:
    """The ladder holds 10, 9 and 8 only, so `7_or_lower` has no floor at all.

    (144.00 + 483.00 + 51.00) / 0.98 = 678.00 / 0.98 = 691.84.
    """
    result = answer(
        expected_value(
            distribution(SPEC_EXAMPLE),
            prices({"10": "1200.00", "9": "700.00", "8": "300.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("691.84")
    assert result.unpriced_grades == (Grade.parse("7_or_lower"),)
    assert result.unpriced_probability == pytest.approx(0.02)


def test_one_bucket_does_not_price_another() -> None:
    """Only points are scanned, so a bucket price answers for its own key alone.

    `6_or_lower` is not a grade that `7_or_lower` covers — it is another
    collapsed tail, and reading a floor out of it would stack one estimate on
    top of another.
    """
    result = answer(
        expected_value(
            distribution({"9": 0.98, "7_or_lower": 0.02}),
            prices({"9": "500.00", "6_or_lower": "5.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.amount == Money.of("500.00")
    assert result.unpriced_grades == (Grade.parse("7_or_lower"),)


# --------------------------------------------------------------------------
# Confidence propagation
# --------------------------------------------------------------------------


def test_confidence_is_the_distribution_confidence_times_the_weighted_price_confidence() -> None:
    """0.80 · (0.60·0.90 + 0.40·0.50) = 0.80 · 0.74 = 0.592.

    Weighted by probability, so a stale price on an unlikely grade costs little
    and a stale price on the likely one costs a lot.
    """
    ladder = {
        Grade.parse("9"): GradedPrice(Money.of("400.00"), Confidence.of(0.90)),
        Grade.parse("10"): GradedPrice(Money.of("1000.00"), Confidence.of(0.50)),
    }

    result = answer(
        expected_value(
            distribution({"9": 0.60, "10": 0.40}),
            ladder,
            distribution_confidence=Confidence.of(0.80),
        )
    )

    assert result.confidence.value == pytest.approx(0.592)


def test_unpriced_mass_costs_confidence_without_a_separate_penalty() -> None:
    """A tenth of the distribution is unpriced, so a certain answer is 90% sure.

    The weighting runs over the *original* probabilities, so an unpriced grade
    contributes nothing and the renormalisation penalty falls out of the same
    sum rather than needing a term of its own.
    """
    result = answer(
        expected_value(
            distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
            prices({"9": "400.00", "8": "150.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence.value == pytest.approx(0.90)


def test_certainty_everywhere_is_certainty() -> None:
    result = answer(
        expected_value(
            distribution({"10": 0.10, "9": 0.60, "8": 0.30}),
            prices({"10": "1000.00", "9": "400.00", "8": "150.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence == CERTAIN


def test_confidence_cannot_exceed_one_when_the_distribution_sums_just_over() -> None:
    """Spec §63 is `Σ P(g) ≈ 1`, so the weighted sum can land just above 1.

    `{"9": 0.5000005, "8": 0.5}` is a distribution `GradeDistribution` accepts —
    5e-7 from 1, inside `SUM_TOLERANCE`. Multiplying certain confidences by it
    gives 1.0000005, which `Confidence` refuses. The clamp is what keeps a
    legal input from raising.
    """
    result = answer(
        expected_value(
            distribution({"9": 0.5000005, "8": 0.5}),
            prices({"9": "400.00", "8": "150.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence == CERTAIN


def test_a_bucket_carries_the_confidence_of_the_price_it_was_valued_at() -> None:
    """The floor's confidence, not the ladder's best: 0.5·1.0 + 0.5·0.40 = 0.70."""
    ladder = {
        Grade.parse("9"): GradedPrice(Money.of("500.00"), CERTAIN),
        Grade.parse("6"): GradedPrice(Money.of("40.00"), Confidence.of(0.40)),
    }

    result = answer(
        expected_value(
            distribution({"9": 0.5, "7_or_lower": 0.5}),
            ladder,
            distribution_confidence=CERTAIN,
        )
    )

    assert result.confidence.value == pytest.approx(0.70)


def test_zero_distribution_confidence_still_yields_an_amount() -> None:
    """An untrusted model is not an absent one — the figure is still computed."""
    result = answer(
        expected_value(
            distribution({"9": 0.5, "10": 0.5}),
            prices({"9": "200.00", "10": "320.00"}),
            distribution_confidence=Confidence.of(0.0),
        )
    )

    assert result.amount == Money.of("260.00")
    assert result.confidence == Confidence.of(0.0)


# --------------------------------------------------------------------------
# Rejections
# --------------------------------------------------------------------------


def test_an_invalid_distribution_never_reaches_the_calculation() -> None:
    """Spec §63 is enforced in `GradeDistribution`'s constructor.

    There is no guard here because there is nothing for one to catch: an
    invalid distribution is impossible to construct, so it is impossible to
    hand over.
    """
    with pytest.raises(InvalidGradeDistribution):
        GradeDistribution.from_mapping({"9": 0.6, "8": 0.2})


def test_a_negative_price_is_refused() -> None:
    """A market price is not negative, and a netted one cannot be either.

    `SellingFee.on` caps the fee at the sale price precisely so #60's
    pre-netted ladder stays non-negative; this is the second line of defence,
    for a ladder that did not come through it.
    """
    with pytest.raises(InvalidGradedPrice):
        GradedPrice(Money.of("-0.01"), CERTAIN)


def test_a_price_that_is_not_money_is_refused() -> None:
    with pytest.raises(InvalidGradedPrice):
        GradedPrice(Decimal("10.00"), CERTAIN)  # type: ignore[arg-type]


def test_a_float_price_is_refused() -> None:
    """0.1 is already not one tenth before any arithmetic happens."""
    with pytest.raises(InvalidGradedPrice):
        GradedPrice(10.0, CERTAIN)  # type: ignore[arg-type]


def test_a_bare_float_confidence_is_refused() -> None:
    """A "confidence" of 87 meaning 87% is the mistake `Confidence` exists for."""
    with pytest.raises(InvalidGradedPrice):
        GradedPrice(Money.of("10.00"), 0.9)  # type: ignore[arg-type]


def test_a_distribution_confidence_is_required() -> None:
    """No default, because the favourable default is the failure mode.

    Assuming 1.0 for a model nobody measured is the same lie as pricing an
    unknown grade at zero, pointed the other way.
    """
    with pytest.raises(TypeError):
        expected_value(  # type: ignore[call-arg]
            distribution({"9": 1.0}),
            prices({"9": "400.00"}),
        )


def test_a_ladder_in_two_currencies_is_refused() -> None:
    """The engine takes SGD and never converts — #53 owns the conversion.

    `Currency` has one member in V1, so a second currency cannot be built
    through the public API; it is forced onto a validated amount the way
    `test_domain_money.py` does it. The guard reads the field, which is what
    matters.
    """
    ladder = prices({"9": "400.00", "8": "150.00"})
    object.__setattr__(ladder[Grade.parse("8")].value, "currency", "USD")

    with pytest.raises(CurrencyMismatch):
        expected_value(
            distribution({"9": 0.6, "8": 0.4}),
            ladder,
            distribution_confidence=CERTAIN,
        )


# --------------------------------------------------------------------------
# The result
# --------------------------------------------------------------------------


def test_a_result_is_frozen() -> None:
    result = answer(
        expected_value(
            distribution({"9": 1.0}),
            prices({"9": "400.00"}),
            distribution_confidence=CERTAIN,
        )
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.amount = Money.zero()  # type: ignore[misc]
