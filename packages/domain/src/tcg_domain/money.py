"""Money — exact decimal arithmetic for the economic engine.

V1 prices everything in SGD. The `currency` field exists anyway so that adding
another currency later is a configuration choice rather than a rewrite of every
signature that carries a price.

Amounts are :class:`~decimal.Decimal`, quantised to two places with
``ROUND_HALF_UP``. Binary floats are rejected outright rather than converted:
the economic engine sums fees, shipping and sale proceeds, and a value that
starts as ``0.1`` is already wrong before any arithmetic happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from enum import StrEnum
from functools import total_ordering
from typing import Final

from tcg_domain.errors import CurrencyMismatch, InvalidMoney

__all__ = ["Currency", "Money"]

#: Every monetary amount is stored to the cent.
CENTS: Final = Decimal("0.01")


class Currency(StrEnum):
    """Currencies the domain can denominate.

    V1 is SGD-only (non-SGD currencies are explicitly out of V1 scope). This
    enum is the extension point, not a feature flag: adding a member does not
    by itself make the product multi-currency.
    """

    SGD = "SGD"


def _quantised(amount: Decimal) -> Decimal:
    """Round an amount to the cent, half away from zero."""
    if isinstance(amount, float):
        raise InvalidMoney(
            f"monetary amounts must be Decimal, not float: {amount!r}. "
            "Use Money.of('12.34') — binary floats cannot represent cents exactly."
        )
    if not isinstance(amount, Decimal):
        raise InvalidMoney(f"monetary amounts must be Decimal, got {type(amount).__name__}")
    if not amount.is_finite():
        raise InvalidMoney(f"monetary amounts must be finite, got {amount!r}")
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    """An exact monetary amount in a single currency.

    Args:
        amount: A `Decimal`. A `float` is rejected; see :meth:`of` for the
            lenient entry point.
        currency: Defaults to :attr:`Currency.SGD`.

    Raises:
        InvalidMoney: If the amount is not a finite `Decimal`, or the currency
            is not a :class:`Currency`.
    """

    amount: Decimal
    currency: Currency = Currency.SGD

    def __post_init__(self) -> None:
        if not isinstance(self.currency, Currency):
            raise InvalidMoney(f"unsupported currency: {self.currency!r}")
        object.__setattr__(self, "amount", _quantised(self.amount))

    # ----------------------------------------------------------------
    # Construction
    # ----------------------------------------------------------------

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: Currency = Currency.SGD) -> Money:
        """Build an amount from a `Decimal`, an `int` or a decimal string.

        Raises:
            InvalidMoney: If `amount` is a `float`, or a string that is not a
                decimal number.
        """
        if isinstance(amount, float):
            raise InvalidMoney(
                f"monetary amounts must not be float: {amount!r}. "
                "Pass a string — Money.of('12.34') — to keep the value exact."
            )
        if isinstance(amount, bool) or not isinstance(amount, (Decimal, int, str)):
            raise InvalidMoney(f"unsupported monetary amount: {amount!r}")
        try:
            return cls(Decimal(amount), currency)
        except (DecimalException, ValueError) as exc:
            raise InvalidMoney(f"unsupported monetary amount: {amount!r}") from exc

    @classmethod
    def zero(cls, currency: Currency = Currency.SGD) -> Money:
        """Nothing, in `currency` — the identity for :meth:`__add__`."""
        return cls(Decimal(0), currency)

    # ----------------------------------------------------------------
    # Arithmetic
    # ----------------------------------------------------------------

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency} with {other.currency}; convert first"
            )

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: int | Decimal) -> Money:
        """Scale an amount by a count or an exact rate (a fee percentage)."""
        if isinstance(factor, float):
            raise InvalidMoney(
                f"cannot scale money by a float: {factor!r}. "
                "Use Decimal('0.075') so the rate is exact."
            )
        if isinstance(factor, bool) or not isinstance(factor, (int, Decimal)):
            return NotImplemented
        return Money(self.amount * Decimal(factor), self.currency)

    def __rmul__(self, factor: int | Decimal) -> Money:
        return self.__mul__(factor)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    # ----------------------------------------------------------------
    # Comparison
    # ----------------------------------------------------------------

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount < other.amount

    def __str__(self) -> str:
        return f"{self.currency} {self.amount}"
