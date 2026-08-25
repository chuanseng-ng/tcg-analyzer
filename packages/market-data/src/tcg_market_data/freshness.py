"""How old a price is, and what it is still worth — spec §38.

    The economic engine should report:

        price_confidence
        price_age

Two functions, and both of them take the moment of asking as an argument.
That is the whole design: **age is a question, not a column.** A stored
`price_age` is wrong the second after it is written, and a stored
`price_confidence` derived from one is wrong in the same way but harder to
notice — which is why #50 gave `market_observations` an `observed_at` and a
`confidence` and neither of these, and why `PriceObservation.confidence` is
documented as the provider's own signal and explicitly "not staleness".

`confidence` and `price_confidence` are therefore different numbers with
similar names, deliberately. The first is how much the provider thought that
one figure was worth — sample size, spread. The second is that, discounted for
how long ago it was true. Only the second is fit to show a user.

**The decay bottoms out above zero.** Spec §38 forbids substituting stale data
*"without identifying it"*, not using it, and §2.7 makes uncertainty a
legitimate output rather than a failure. A month-old price on a thinly traded
card is often the only evidence there is; reporting it at
:data:`STALE_FLOOR` of the provider's confidence says "old, and we know it",
where reporting it at zero would be indistinguishable from having nothing and
would push the recommendation to `insufficient_information` for a card that has
perfectly serviceable evidence.

What is *not* here: any judgement about whether a given confidence is good
enough to act on. That is the economic engine's, and M5's — #55 deliberately
stops at the number.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from tcg_domain.confidence import Confidence

from tcg_market_data.errors import InvalidMarketObservation
from tcg_market_data.port import PriceObservation

__all__ = [
    "FRESH_WITHIN",
    "STALE_FLOOR",
    "price_age",
    "price_confidence",
]

#: One ingestion cycle. Spec §37 targets a once-per-day refresh, so a price up
#: to a day old is not stale — it is simply the current one, and discounting it
#: would report every price in the system as slightly suspect.
FRESH_WITHIN: Final = timedelta(days=1)

#: What a price past the staleness threshold retains, as a fraction of the
#: provider's own confidence. Above zero on purpose: see the module docstring.
STALE_FLOOR: Final = 0.05


def _aware(at: datetime) -> datetime:
    """Refuse a naive moment of asking, naming the argument.

    Comparing a naive `at` against an aware `observed_at` raises `TypeError`
    from inside `datetime`, which says nothing about which of the two the
    caller got wrong. `PriceObservation` already guarantees the other side.
    """
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise InvalidMarketObservation(f"at must be a timezone-aware datetime, got {at!r}")
    return at


def price_age(observation: PriceObservation, *, at: datetime) -> timedelta:
    """How long before `at` the price was observed, never less than nothing.

    A provider whose clock runs ahead of ours is ordinary and is not an error;
    a negative age is neither, and would let :func:`price_confidence` score an
    observation *above* what the provider claimed for it just for arriving
    early. So the future clamps to zero rather than raising.

    Raises:
        InvalidMarketObservation: If `at` is naive.
    """
    elapsed = _aware(at) - observation.observed_at
    return max(elapsed, timedelta(0))


def price_confidence(
    observation: PriceObservation, *, at: datetime, stale_after: timedelta
) -> Confidence:
    """The provider's confidence in this price, discounted for its age.

    Flat at the provider's own figure through :data:`FRESH_WITHIN`, then
    falling in a straight line to :data:`STALE_FLOOR` of it at `stale_after`,
    and never below.

    The decay **multiplies** rather than replaces, so a weak observation stays
    weak: a fresh price the provider was only 40% sure of does not become a
    strong signal for being new. And nothing about the *amount* enters the
    calculation — a card observed to be worth nothing this morning is a
    confident measurement, which is the distinction the whole port exists to
    keep.

    Args:
        observation: The price in question.
        at: The moment of asking. Timezone-aware.
        stale_after: How old a price has to be before it is worth only the
            floor. Configuration, because it is a judgement about this
            deployment's ingestion cadence rather than a fact about prices;
            the API reads it from `TCG_API_MARKET_STALE_AFTER_DAYS`.

    Raises:
        InvalidMarketObservation: If `at` is naive.
        ValueError: If `stale_after` is not longer than :data:`FRESH_WITHIN`.
            Refused rather than clamped — a deployment that set it by accident
            would otherwise report every price older than a day at the floor,
            which looks exactly like a provider outage.

    ponytail: a straight line, because the inputs to anything better do not
    exist yet. A card's price decays at a rate set by how thinly it trades, and
    the volatility signal that would carry that arrives with M5's economic
    engine; revisit then, not before.
    """
    if stale_after <= FRESH_WITHIN:
        raise ValueError(
            f"stale_after must be longer than FRESH_WITHIN ({FRESH_WITHIN}), got {stale_after}"
        )
    age = price_age(observation, at=at)
    if age <= FRESH_WITHIN:
        return observation.confidence
    if age >= stale_after:
        return Confidence.of(observation.confidence.value * STALE_FLOOR)
    decaying = (age - FRESH_WITHIN) / (stale_after - FRESH_WITHIN)
    factor = 1.0 - (1.0 - STALE_FLOOR) * decaying
    return Confidence.of(observation.confidence.value * factor)
