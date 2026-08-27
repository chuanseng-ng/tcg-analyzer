"""The economic engine's exception hierarchy.

Every error raised here derives from :class:`EconomicEngineError`, so a caller
can catch the whole package with one clause, and each concrete error also
derives from the closest builtin — the convention :mod:`tcg_domain.errors` sets
and :mod:`tcg_market_data.errors` follows.

**Nothing here expresses an unanswerable question.** "There is no price for this
grade" and "grading this is not clearly worthwhile" are *results*, carried by
:data:`tcg_domain.confidence.INSUFFICIENT_INFORMATION` (spec §2.7, §44), and
ADR 0007 makes an ROI with no capital at risk a ``null`` with a stated reason
rather than an error. The errors below mean the caller handed the engine
something it cannot represent at all.
"""

from __future__ import annotations

__all__ = [
    "EconomicEngineError",
    "InvalidAcquisitionCost",
    "InvalidComparison",
    "InvalidCostConfiguration",
    "InvalidGradedPrice",
    "UnknownOptimizationMode",
]


class EconomicEngineError(Exception):
    """Base class for every error raised by this package."""


class InvalidCostConfiguration(EconomicEngineError, ValueError):
    """A cost configuration is not representable.

    Covers a line item that is not a :class:`~tcg_domain.money.Money`, a
    negative one, and a selling fee whose rate falls outside ``[0, 1]`` or
    whose flat component is negative.

    A `float` amount is refused for the same reason
    :class:`~tcg_domain.errors.InvalidMoney` refuses one — it is already wrong
    before any arithmetic happens — but it never reaches `Money` here, because
    a float is simply not a line item.
    """


class InvalidAcquisitionCost(EconomicEngineError, ValueError):
    """An acquisition cost was supplied and could not be read.

    Deliberately **not** :class:`InvalidCostConfiguration`. Spec §45 makes the
    acquisition cost optional user input and #58 keeps it out of
    :class:`~tcg_economic_engine.costs.CostConfiguration` entirely, so an error
    naming the configuration would say the opposite of what the model does.

    Note what this is *not*. `None` is **absence**, which ADR 0007 reports as
    ``insufficient_information`` with reason ``acquisition_cost_not_supplied`` —
    "I don't remember what I paid" is a legitimate state of the world, not a
    caller's mistake. This error means a value arrived and was a `float`, was
    not a :class:`~tcg_domain.money.Money`, or was negative.

    The negative case is load-bearing rather than defensive, for the reason
    :meth:`~tcg_economic_engine.costs.SellingFee.on`'s cap is: ADR 0007 asserts
    that neither ``CapitalAtRisk`` denominator can be negative "because both are
    sums of non-negative quantities", and ``CapitalAtRisk_inv`` is
    ``acquisition_cost + grading_costs``. Refusing one here is what keeps that
    claim true before #62 divides by it.
    """


class InvalidGradedPrice(EconomicEngineError, ValueError):
    """A graded market price is not representable.

    Covers a value that is not a :class:`~tcg_domain.money.Money` — a `float`
    above all — a negative one, and a confidence that is not a
    :class:`~tcg_domain.confidence.Confidence`.

    Note what this is *not*. "No price is held for this grade" is not an error
    and is not raised: it is absence, which
    :func:`~tcg_economic_engine.expectation.expected_value` records in
    ``unpriced_grades`` and renormalises around. This error means a price was
    supplied and could not be read.
    """


class UnknownOptimizationMode(EconomicEngineError, KeyError):
    """No strategy carries the mode name a caller asked for.

    Spec §43 fixes five modes and requires the architecture to admit more, so
    :func:`~tcg_economic_engine.strategies.rank` takes a strategy *object* and a
    mode nobody registered still ranks. This is raised only by
    :func:`~tcg_economic_engine.strategies.strategy_for`, which is the lookup
    from §43's names — a caller that hands over its own strategy never sees it.

    A caller's mistake rather than an unanswerable question: "we do not know what
    you meant by `blended_score`" is not a result about a card, and #65 turns it
    into a 422 rather than a §66 code.
    """


class InvalidComparison(EconomicEngineError, ValueError):
    """A set of company outlooks cannot be compared.

    Today that means two outlooks naming the same company, which would list one
    company twice and leave "best" meaning nothing. Also a caller's mistake: the
    companies to compare are the application's own, not user input.
    """
