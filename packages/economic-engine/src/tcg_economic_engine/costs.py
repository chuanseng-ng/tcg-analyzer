"""Spec §46's cost line items, in SGD, with exact decimal arithmetic.

§46 fixes six configurable costs — ``grading_fee``, ``outbound_shipping``,
``return_shipping``, ``insurance``, ``miscellaneous`` and ``selling_fee`` — and
§47 lists the dimensions a later version adds: country, currency, grading
company, service tier, declared value, shipping provider, tax. "The underlying
economic model should already support these as separate line items."

**Which is why each cost is its own named field and nothing here computes a
grand total.** A §47 dimension attaches to one line rather than to all of them
— tax applies to some and not others, shipping varies by provider, the grading
fee varies by tier — so a single total would make every one of them a rewrite.
There is deliberately no generic line-item container: named fields *are*
separate line items, and a container with one shape and one use today would be
an abstraction guessing at §47's shape rather than admitting it.

**The selling fee is not one of the five.** :attr:`CostConfiguration.grading_costs`
sums five of §46's six, and ADR 0007 explains at length why: the fee is paid out
of proceeds rather than committed up front, so it belongs in the numerator of
both profit figures and in neither ``CapitalAtRisk`` denominator. Completing the
sum to six breaks both ratios.

**The fee's shape is this module's decision**, which ADR 0007 says outright:
"the shape of the fee is #58's decision rather than this one's". §46 admits a
percentage, a flat amount, or both, so :class:`SellingFee` carries a rate and a
flat component and charges their sum — capped at the sale price. See
:meth:`SellingFee.on` for why the cap is load-bearing rather than defensive.

Everything is :class:`~tcg_domain.money.Money`, so amounts are `Decimal`
quantised to the cent and binary floats are refused at the boundary. #53 owns
the USD→SGD conversion for ingested prices; the economic engine takes SGD and
never converts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from tcg_domain import Money

from tcg_economic_engine.errors import InvalidCostConfiguration

__all__ = ["CostConfiguration", "SellingFee"]

#: A fee may take none of a sale, or all of it, and nothing outside that.
MIN_RATE: Final = Decimal(0)
MAX_RATE: Final = Decimal(1)

#: The five line items committed before a sale happens — see
#: :attr:`CostConfiguration.grading_costs`. The selling fee is deliberately not
#: among them.
COMMITTED_LINE_ITEMS: Final = (
    "grading_fee",
    "outbound_shipping",
    "return_shipping",
    "insurance",
    "miscellaneous",
)


def _validated_rate(value: object) -> Decimal:
    """Check a proportion of the sale price.

    Typed `object` rather than `Decimal` for the reason
    ``tcg_domain.money._quantised`` is: this is a trust boundary — rates arrive
    from JSON payloads and untyped callers — and a `Decimal` annotation would
    make the float rejection below unreachable *to the type checker* while it
    stays entirely reachable at runtime, which is the exact combination that
    gets a real guard deleted as dead code.
    """
    if isinstance(value, float):
        raise InvalidCostConfiguration(
            f"a selling-fee rate must be Decimal, not float: {value!r}. "
            "Use Decimal('0.10') — binary floats cannot represent a rate exactly."
        )
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise InvalidCostConfiguration(
            f"a selling-fee rate must be Decimal, got {type(value).__name__}"
        )
    if not value.is_finite():
        raise InvalidCostConfiguration(f"a selling-fee rate must be finite, got {value!r}")
    if not MIN_RATE <= value <= MAX_RATE:
        raise InvalidCostConfiguration(
            f"a selling-fee rate must be a proportion in [0, 1], got {value!r}. "
            "Ten percent is Decimal('0.10'), not Decimal('10')."
        )
    return value


def _validated_amount(name: str, value: object) -> Money:
    """Check one cost line item. Typed `object` for the reason above."""
    if not isinstance(value, Money):
        raise InvalidCostConfiguration(
            f"{name} must be Money, got {type(value).__name__}. "
            "Use Money.of('40.00') so the amount stays exact."
        )
    if value < Money.zero(value.currency):
        raise InvalidCostConfiguration(f"{name} must not be negative, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class SellingFee:
    """What selling a card costs: a proportion of the price plus a flat part.

    Spec §46 lists ``selling_fee`` as one configurable input without fixing its
    shape; marketplaces charge a commission, a fixed listing or payment fee, or
    both, so both are here and either may be zero.

    Args:
        rate: The proportion of the realised sale price taken as commission, in
            ``[0, 1]``. Ten percent is ``Decimal("0.10")``.
        flat: A fixed amount charged per sale regardless of price.

    Raises:
        InvalidCostConfiguration: If `rate` is not a finite `Decimal` in
            ``[0, 1]`` — a `float` included — or `flat` is not a non-negative
            `Money`.
    """

    rate: Decimal = MIN_RATE
    flat: Money = field(default_factory=Money.zero)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", _validated_rate(self.rate))
        object.__setattr__(self, "flat", _validated_amount("a flat selling fee", self.flat))

    def on(self, sale_price: Money) -> Money:
        """What selling at `sale_price` costs.

        ADR 0007 charges this to both branches of the incremental comparison —
        a raw sale incurs it too — and applies it **per grade outcome, inside
        the sum**, never to an expected value.

        **The fee is capped at the sale price**, so proceeds are never negative.
        That is not defensive rounding: ADR 0007 states that neither
        ``CapitalAtRisk`` denominator can be negative "because both are sums of
        non-negative quantities", and ``raw_opportunity_value`` is
        ``raw_market_value`` less ``sale_costs(raw_market_value)``. An uncapped flat
        fee on a cheap card would falsify that claim in #60 rather than here.
        Capping once, where every caller routes through, is the whole fix.

        The cap makes the fee piecewise-affine rather than affine, which is
        precisely the case ADR 0007's apply-it-inside-the-sum rule exists for —
        so nothing downstream changes, and netting fees off an expected value
        afterwards is now measurably wrong rather than merely fragile.
        """
        return min(sale_price * self.rate + self.flat, sale_price)


@dataclass(frozen=True, slots=True)
class CostConfiguration:
    """Spec §46's six cost line items, all in SGD and all user-overridable.

    Frozen, because §57's reproducibility record names the configuration an
    analysis was computed against: once an analysis references one, re-running
    with different costs is a new analysis rather than an edit. Override with
    :func:`dataclasses.replace`.

    The defaults are **illustrative placeholders** for a Singapore submission,
    not quoted rates — §46 rules out a regional pricing system in V1 and this
    package fetches nothing from a grading company's site. They exist because an
    all-zero default would report grading as costless and bias every
    recommendation toward `grade`, which is the failure ADR 0007 rejects for a
    zeroed acquisition cost. Every one of them is meant to be replaced by what
    the user was actually quoted.

    Args:
        grading_fee: The company's charge per card for the chosen tier.
        outbound_shipping: Getting the card to the grader.
        return_shipping: Getting it back. Separate from outbound because the two
            differ in practice and §47's shipping-provider dimension attaches to
            each independently.
        insurance: Cover for the round trip.
        miscellaneous: Anything else — sleeves, semi-rigids, a courier surcharge.
        selling_fee: What selling costs, once. Not part of
            :attr:`grading_costs`; see that property for why.

    Raises:
        InvalidCostConfiguration: If any line item is not a non-negative
            `Money`, or `selling_fee` is not a :class:`SellingFee`.
    """

    grading_fee: Money = field(default_factory=lambda: Money.of("40.00"))
    outbound_shipping: Money = field(default_factory=lambda: Money.of("30.00"))
    return_shipping: Money = field(default_factory=lambda: Money.of("30.00"))
    insurance: Money = field(default_factory=Money.zero)
    miscellaneous: Money = field(default_factory=Money.zero)
    selling_fee: SellingFee = field(default_factory=lambda: SellingFee(rate=Decimal("0.10")))

    def __post_init__(self) -> None:
        for name in COMMITTED_LINE_ITEMS:
            object.__setattr__(self, name, _validated_amount(name, getattr(self, name)))
        if not isinstance(self.selling_fee, SellingFee):
            raise InvalidCostConfiguration(
                f"selling_fee must be a SellingFee, got {type(self.selling_fee).__name__}. "
                "A bare rate is ambiguous: §46 admits a percentage and a flat amount."
            )

    @property
    def grading_costs(self) -> Money:
        """What committing to grade this card costs — **five** line items.

        The selling fee is deliberately absent. ADR 0007 puts it in proceeds
        rather than in ``CapitalAtRisk``, because it is paid out of a sale that
        may not happen rather than committed up front, and both the incremental
        and the investment ratio are built on this sum. Adding the sixth item
        here double-counts it and breaks both.
        """
        total = Money.zero(self.grading_fee.currency)
        for name in COMMITTED_LINE_ITEMS:
            item: Money = getattr(self, name)
            total = total + item
        return total
