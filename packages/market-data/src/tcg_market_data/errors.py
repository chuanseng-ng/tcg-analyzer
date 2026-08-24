"""The market-data package's exception hierarchy.

Every error raised here derives from :class:`MarketDataError`, so a caller can
catch the whole package with one clause, and each concrete error also derives
from the closest builtin — the convention :mod:`tcg_domain.errors` sets and
:mod:`tcg_shared.storage.errors` follows, so handling one of these never
requires knowing this package's private hierarchy.

**Nothing here expresses unavailability.** "There is no price for this card" is
a *result*, carried by :data:`tcg_domain.confidence.INSUFFICIENT_INFORMATION`
(spec §38, §2.7). The errors below mean the caller asked for something the
package cannot answer at all — the provider was unreachable, or the record was
not representable.

**:class:`MarketProviderUnavailable` is named for the provider, not the data,
and that is deliberate.** It maps to spec §66's ``provider_error``, **never** to
``market_data_unavailable``: ``services/api/src/tcg_api/errors.py`` records that
the two are "deliberately distinct — the first means there is no usable price,
the second that the call failed", and a class called ``MarketDataUnavailable``
is exactly how a caller ends up choosing the wrong one.

The hierarchy exists so that **no caller ever sees a provider's exception
type**. An HTTP client's error escaping #52's adapter would make the concrete
vendor part of the calling contract, and the whole point of the port is that
providers are replaceable (CLAUDE.md, "external providers are replaceable").
Adapters translate; they do not re-raise.
"""

from __future__ import annotations

__all__ = [
    "InvalidMarketObservation",
    "MarketDataError",
    "MarketProviderUnavailable",
]


class MarketDataError(Exception):
    """Base class for every error raised by this package."""


class InvalidMarketObservation(MarketDataError, ValueError):
    """A price observation is not representable.

    Covers a naive ``observed_at``, a negative price, a provider that is not a
    lowercase slug, and a record that is half-graded — carrying a grading
    company without a grade, or the reverse. That last is the Python side of
    spec §35's rule that ``grading_company`` and ``grade`` are null for a raw
    observation and required for a graded one; #50 enforces the same thing in
    SQL, and neither is a substitute for the other.

    A grade that is simply not on its company's scale is **not** this error —
    see :func:`tcg_market_data.port.validated_grade_key`.
    """


class MarketProviderUnavailable(MarketDataError, ConnectionError):
    """The market-data provider could not be reached, or refused credentials.

    Not invalid input, and not an absent price: the request was well-formed,
    the card may well have a price, and the provider simply did not answer.
    Also a `ConnectionError`, because that is what it is, and because retry
    logic written against the builtin should apply here unchanged.
    """
