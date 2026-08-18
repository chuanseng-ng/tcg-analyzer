"""What the identification pipeline concluded, and how sure it is.

Spec §20 lists the pipeline's output — game, language, set, card number, name,
variant, confidence — and then states the rule this module exists to enforce:

    The user must confirm the result.

    Never silently use an uncertain card identification for economic analysis.

:class:`Card` already carries the first six. The seventh is the reason this is
a type rather than a bare :class:`~tcg_domain.catalog.Card`, and it is
**required and validated**: an optional confidence, or a bare `float`, makes
the forbidden thing the easy thing. A caller who wants to ignore the number has
to write that down.

Confirmation is not modelled here. It is a state of an analysis (spec §65's
``awaiting_confirmation``), not a property of a conclusion, and putting a
``confirmed`` flag on this value would let a conclusion be edited into a
confirmation.

An identifier that could not conclude anything returns
:data:`~tcg_domain.confidence.INSUFFICIENT_INFORMATION`, so its signature reads
``Uncertain[CardIdentification]``. Failing to identify a card is an answer, not
an exception.
"""

from __future__ import annotations

from dataclasses import dataclass

from tcg_domain.catalog import Card
from tcg_domain.confidence import Confidence
from tcg_domain.errors import InvalidCardIdentification

__all__ = ["CardIdentification"]


@dataclass(frozen=True, slots=True)
class CardIdentification:
    """A candidate card, and how confident the pipeline is that it is the one.

    Args:
        card: The candidate from the canonical catalog.
        confidence: How sure the pipeline is, as a validated
            :class:`~tcg_domain.confidence.Confidence`. Required.

    Raises:
        InvalidCardIdentification: If `card` is not a
            :class:`~tcg_domain.catalog.Card`, or `confidence` is anything
            other than a :class:`~tcg_domain.confidence.Confidence` — a raw
            ``0.82`` is rejected, because only the value object makes
            ``[0, 1]`` true.
    """

    card: Card
    confidence: Confidence

    def __post_init__(self) -> None:
        if not isinstance(self.card, Card):
            raise InvalidCardIdentification(f"card must be a Card, got {type(self.card).__name__}")
        if not isinstance(self.confidence, Confidence):
            raise InvalidCardIdentification(
                "confidence must be a Confidence, got "
                f"{type(self.confidence).__name__} — spec §20 forbids acting on an "
                "uncertain identification, which only holds if the value is validated"
            )

    def __str__(self) -> str:
        return f"{self.card.reference} — {self.confidence}"
