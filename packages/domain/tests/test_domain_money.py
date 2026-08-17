"""`Money` â€” exact decimal arithmetic, denominated in SGD."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from tcg_domain import Currency, CurrencyMismatch, InvalidMoney, Money

# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def test_construction_rejects_a_float() -> None:
    """Binary floats lose cents; money is never float."""
    with pytest.raises(InvalidMoney):
        Money(1.5)  # type: ignore[arg-type]


def test_of_rejects_a_float_too() -> None:
    with pytest.raises(InvalidMoney):
        Money.of(1.5)  # type: ignore[arg-type]


def test_of_accepts_a_string() -> None:
    assert Money.of("12.34").amount == Decimal("12.34")


def test_of_accepts_an_int() -> None:
    assert Money.of(12).amount == Decimal("12.00")


def test_of_accepts_a_decimal() -> None:
    assert Money.of(Decimal("12.34")).amount == Decimal("12.34")


def test_of_rejects_an_unparseable_string() -> None:
    with pytest.raises(InvalidMoney):
        Money.of("twelve dollars")


def test_of_rejects_a_non_finite_amount() -> None:
    with pytest.raises(InvalidMoney):
        Money.of(Decimal("NaN"))


def test_zero() -> None:
    assert Money.zero() == Money.of("0")
    assert Money.zero().amount == Decimal("0.00")


def test_defaults_to_sgd() -> None:
    """V1 prices in SGD; the field exists so another currency is config, not a rewrite."""
    assert Money.of("1").currency is Currency.SGD


def test_amount_is_quantised_to_two_places() -> None:
    assert Money.of("12.3").amount == Decimal("12.30")
    assert str(Money.of("12.3").amount) == "12.30"


def test_quantisation_rounds_half_up() -> None:
    assert Money.of("0.125").amount == Decimal("0.13")
    assert Money.of("0.135").amount == Decimal("0.14")


def test_money_is_frozen() -> None:
    price = Money.of("10")
    with pytest.raises(FrozenInstanceError):
        price.amount = Decimal("0")  # type: ignore[misc]


def test_str_names_the_currency() -> None:
    assert str(Money.of("12.34")) == "SGD 12.34"
    assert str(Money.zero()) == "SGD 0.00"


# --------------------------------------------------------------------------
# Exactness â€” the reason for Decimal
# --------------------------------------------------------------------------


def test_repeated_addition_is_exact() -> None:
    """Summing 0.10 a hundred times is exactly 10.00, never 9.99999999999998."""
    total = Money.zero()
    for _ in range(100):
        total = total + Money.of("0.10")
    assert total == Money.of("10.00")
    assert total.amount == Decimal("10.00")


def test_a_third_of_a_cent_does_not_accumulate() -> None:
    assert Money.of("0.01") * 3 == Money.of("0.03")


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------


def test_addition() -> None:
    assert Money.of("10.50") + Money.of("2.25") == Money.of("12.75")


def test_subtraction() -> None:
    assert Money.of("10.50") - Money.of("2.25") == Money.of("8.25")


def test_subtraction_may_go_negative() -> None:
    """Grading economics can lose money; negatives are legitimate."""
    assert Money.of("2.00") - Money.of("5.00") == Money.of("-3.00")


def test_multiplication_by_an_int() -> None:
    assert Money.of("12.34") * 3 == Money.of("37.02")


def test_multiplication_by_a_decimal() -> None:
    assert Money.of("100.00") * Decimal("0.075") == Money.of("7.50")


def test_multiplication_is_commutative() -> None:
    assert 3 * Money.of("12.34") == Money.of("12.34") * 3


def test_multiplication_by_a_float_is_rejected() -> None:
    with pytest.raises(InvalidMoney):
        _ = Money.of("100.00") * 0.075  # type: ignore[operator]


def test_addition_of_a_non_money_is_rejected() -> None:
    with pytest.raises(TypeError):
        _ = Money.of("1.00") + 1  # type: ignore[operator]


# --------------------------------------------------------------------------
# Currency safety
# --------------------------------------------------------------------------


def in_another_currency(amount: str) -> Money:
    """Build a `Money` denominated in something other than SGD.

    `Currency` has a single member in V1 (spec: non-SGD currencies are out of
    scope), so a second currency cannot be constructed through the public API.
    The mismatch guard still has to be proven, so the currency is forced onto a
    validated instance â€” the guard reads the field, which is what matters.
    """
    money = Money.of(amount)
    object.__setattr__(money, "currency", "USD")
    return money


def test_construction_rejects_a_currency_that_is_not_a_currency() -> None:
    with pytest.raises(InvalidMoney):
        Money(Decimal("1.00"), "USD")  # type: ignore[arg-type]


def test_mixed_currency_addition_raises() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = Money.of("1.00") + in_another_currency("1.00")


def test_mixed_currency_subtraction_raises() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = Money.of("1.00") - in_another_currency("1.00")


def test_mixed_currency_comparison_raises() -> None:
    with pytest.raises(CurrencyMismatch):
        _ = Money.of("1.00") < in_another_currency("2.00")


def test_mixed_currency_equality_is_false_not_an_error() -> None:
    assert Money.of("1.00") != in_another_currency("1.00")


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def test_ordering() -> None:
    assert Money.of("1.00") < Money.of("2.00")
    assert Money.of("2.00") > Money.of("1.00")
    assert Money.of("1.00") <= Money.of("1.00")
    assert Money.of("1.00") >= Money.of("1.00")


def test_equality_ignores_representation() -> None:
    assert Money.of("1.5") == Money.of("1.50")


def test_money_is_hashable() -> None:
    assert len({Money.of("1.50"), Money.of("1.5"), Money.of("2.00")}) == 2


def test_sorting() -> None:
    amounts = [Money.of("3.00"), Money.of("1.00"), Money.of("2.00")]
    assert sorted(amounts) == [Money.of("1.00"), Money.of("2.00"), Money.of("3.00")]
