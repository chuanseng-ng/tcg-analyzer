"""Spec §46's six cost line items, and the fee shape ADR 0007 left to #58.

Every figure asserted here is hand-calculated. Spec §69/M5's acceptance
criterion is that the economics are "independently unit-tested against manually
calculated fixtures", so a number that came out of the implementation is not
evidence — the ones below are read off ADR 0007's worked examples or computed
on paper, and they are written before the module they check.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from tcg_domain import Money
from tcg_economic_engine import CostConfiguration, InvalidCostConfiguration, SellingFee

# ---- Defaults ----------------------------------------------------------


def test_every_line_item_has_a_default() -> None:
    """A configuration nobody has touched is still usable — and not free."""
    config = CostConfiguration()

    assert config.grading_fee == Money.of("40.00")
    assert config.outbound_shipping == Money.of("30.00")
    assert config.return_shipping == Money.of("30.00")
    assert config.insurance == Money.zero()
    assert config.miscellaneous == Money.zero()
    assert config.selling_fee == SellingFee(rate=Decimal("0.10"))


def test_the_defaults_do_not_make_grading_free() -> None:
    """An all-zero default would bias every recommendation toward `grade`.

    Same failure ADR 0007 rejects for a zeroed acquisition cost: a placeholder
    that reads as a measurement.
    """
    assert CostConfiguration().grading_costs > Money.zero()


def test_every_line_item_is_overridable() -> None:
    config = CostConfiguration(
        grading_fee=Money.of("55.00"),
        outbound_shipping=Money.of("12.00"),
        return_shipping=Money.of("8.00"),
        insurance=Money.of("6.50"),
        miscellaneous=Money.of("1.25"),
        selling_fee=SellingFee(rate=Decimal("0.13"), flat=Money.of("0.35")),
    )

    assert config.grading_fee == Money.of("55.00")
    assert config.miscellaneous == Money.of("1.25")
    assert config.selling_fee.rate == Decimal("0.13")


# ---- The selling fee ---------------------------------------------------


def test_a_percentage_fee_is_proportional_to_the_sale_price() -> None:
    """ADR 0007 example 2's three fees, which #60 and #62 consume verbatim."""
    fee = SellingFee(rate=Decimal("0.10"))

    assert fee.on(Money.of("200.00")) == Money.of("20.00")
    assert fee.on(Money.of("320.00")) == Money.of("32.00")
    assert fee.on(Money.of("100.00")) == Money.of("10.00")


def test_a_flat_fee_does_not_vary_with_the_sale_price() -> None:
    fee = SellingFee(flat=Money.of("2.50"))

    assert fee.on(Money.of("200.00")) == Money.of("2.50")
    assert fee.on(Money.of("320.00")) == Money.of("2.50")


def test_a_percentage_and_a_flat_fee_combine() -> None:
    """§46 admits both at once: 10% of 200.00, plus 2.50."""
    fee = SellingFee(rate=Decimal("0.10"), flat=Money.of("2.50"))

    assert fee.on(Money.of("200.00")) == Money.of("22.50")


def test_no_fee_costs_nothing() -> None:
    """ADR 0007 example 1 sells at 100.00 with `sale_costs` of zero."""
    assert SellingFee().on(Money.of("100.00")) == Money.zero()


def test_the_fee_never_exceeds_the_sale_price() -> None:
    """ADR 0007: neither `CapitalAtRisk` denominator can be negative.

    `raw_opportunity_value` is `raw_market_value` less `sale_costs(raw_market_value)`,
    so an uncapped flat fee on a cheap card would make it negative and break
    that claim at #60 rather than here.
    """
    fee = SellingFee(flat=Money.of("5.00"))

    assert fee.on(Money.of("2.00")) == Money.of("2.00")
    assert fee.on(Money.zero()) == Money.zero()


def test_the_fee_is_exact() -> None:
    """7.5% of 133.33 is 9.99975 — 10.00 half-up, and not what a float says."""
    fee = SellingFee(rate=Decimal("0.075"))

    assert fee.on(Money.of("133.33")) == Money.of("10.00")


# ---- grading_costs — five line items, not six --------------------------


def test_grading_costs_sums_the_five_committed_line_items() -> None:
    """ADR 0007 example 1: 40 + 12 + 8 + 0 + 0."""
    config = CostConfiguration(
        grading_fee=Money.of("40.00"),
        outbound_shipping=Money.of("12.00"),
        return_shipping=Money.of("8.00"),
        insurance=Money.zero(),
        miscellaneous=Money.zero(),
    )

    assert config.grading_costs == Money.of("60.00")


def test_grading_costs_excludes_the_selling_fee() -> None:
    """The gap is a decision, not an omission — ADR 0007.

    The fee is paid out of proceeds rather than committed up front, so adding
    it here would double-count it and break both ROI denominators. This test
    is what fails when a reviewer "completes" the sum to §46's six items.
    """
    config = CostConfiguration(
        grading_fee=Money.of("40.00"),
        outbound_shipping=Money.of("12.00"),
        return_shipping=Money.of("8.00"),
        selling_fee=SellingFee(rate=Decimal("0.10"), flat=Money.of("999.00")),
    )

    assert config.grading_costs == Money.of("60.00")


# ---- Rejections --------------------------------------------------------


def test_a_float_line_item_is_rejected() -> None:
    """A float never reaches `Money`: it is not one, and is refused as not one."""
    with pytest.raises(InvalidCostConfiguration):
        CostConfiguration(grading_fee=40.0)  # type: ignore[arg-type]


def test_a_float_rate_is_rejected() -> None:
    """0.1 is already not one tenth before any arithmetic happens."""
    with pytest.raises(InvalidCostConfiguration):
        SellingFee(rate=0.1)  # type: ignore[arg-type]


def test_a_negative_line_item_is_rejected() -> None:
    with pytest.raises(InvalidCostConfiguration):
        CostConfiguration(insurance=Money.of("-1.00"))


def test_a_negative_flat_fee_is_rejected() -> None:
    with pytest.raises(InvalidCostConfiguration):
        SellingFee(flat=Money.of("-0.01"))


@pytest.mark.parametrize("rate", ["-0.01", "1.01"])
def test_a_rate_outside_the_unit_interval_is_rejected(rate: str) -> None:
    """A fee taking more than the whole sale price is a configuration error."""
    with pytest.raises(InvalidCostConfiguration):
        SellingFee(rate=Decimal(rate))


def test_a_selling_fee_of_the_wrong_type_is_rejected() -> None:
    with pytest.raises(InvalidCostConfiguration):
        CostConfiguration(selling_fee=Decimal("0.10"))  # type: ignore[arg-type]


# ---- Immutability ------------------------------------------------------


def test_a_configuration_cannot_be_mutated() -> None:
    """§57's reproducibility record depends on this: a re-run is a new one."""
    config = CostConfiguration()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.grading_fee = Money.of("1.00")  # type: ignore[misc]


def test_overriding_leaves_the_original_untouched() -> None:
    """`dataclasses.replace` is the override path — there is no setter."""
    original = CostConfiguration()

    amended = dataclasses.replace(original, insurance=Money.of("6.50"))

    assert amended.insurance == Money.of("6.50")
    assert original.insurance == Money.zero()
    assert amended.grading_fee == original.grading_fee
