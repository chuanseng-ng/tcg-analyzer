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

__all__ = ["EconomicEngineError", "InvalidCostConfiguration", "InvalidGradedPrice"]


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
