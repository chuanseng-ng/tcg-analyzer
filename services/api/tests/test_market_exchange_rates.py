"""What `tcg-record-exchange-rate` refuses before it reaches the database.

The CHECKs on `exchange_rates` are the guarantee; this is the sentence an
operator reads instead of a constraint name. Runs without PostgreSQL.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tcg_api.market.exchange_rates import ExchangeRateRefused, verify_rate


def test_a_usd_rate_is_accepted() -> None:
    verify_rate(base_currency="USD", rate=Decimal("1.3421"))


@pytest.mark.parametrize("base", ["usd", "US", "DOLLAR", ""])
def test_a_base_that_is_not_an_iso_code_is_refused(base: str) -> None:
    with pytest.raises(ExchangeRateRefused, match="ISO 4217"):
        verify_rate(base_currency=base, rate=Decimal("1.3"))


def test_an_sgd_to_sgd_rate_is_refused() -> None:
    """An SGD quote is stored unconverted, so such a rate is one nothing may use."""
    with pytest.raises(ExchangeRateRefused, match="SGD"):
        verify_rate(base_currency="SGD", rate=Decimal("1"))


@pytest.mark.parametrize("value", ["0", "-1.3", "NaN", "Infinity"])
def test_a_rate_that_is_not_positive_and_finite_is_refused(value: str) -> None:
    with pytest.raises(ExchangeRateRefused, match="positive"):
        verify_rate(base_currency="USD", rate=Decimal(value))


def test_a_rate_finer_than_the_column_is_refused_rather_than_rounded() -> None:
    """NUMERIC(18, 8): a ninth place would be silently rounded into another rate."""
    with pytest.raises(ExchangeRateRefused, match="8 decimal places"):
        verify_rate(base_currency="USD", rate=Decimal("1.342156789"))
