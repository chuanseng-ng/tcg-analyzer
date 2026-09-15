"""The ingestion side of the port — issue #54.

`MarketDataProvider` answers one card at a time for a reader. A daily run over a
whole catalog wants the other shape: a batch of the provider's own identifiers
in, the quotes it said out, with nothing converted, mapped or bound-checked —
all three are normalization's (#53). #52's adapter implements this.

Driven through a `QuoteSource`-annotated binding, for the reason
`test_market_provider_contract.py` gives: an annotation nobody calls proves
nothing outside a developer's mypy run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from tcg_market_data import MarketProviderUnavailable, ProviderQuote, QuoteSource

SEEN_AT = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)


class _Source:
    provider = "examplesource"

    def __init__(self, *, down: bool = False) -> None:
        self.down = down

    async def fetch(self, external_ids: Sequence[str]) -> Sequence[ProviderQuote]:
        if self.down:
            raise MarketProviderUnavailable("the provider did not answer")
        return tuple(
            ProviderQuote(
                external_id=identifier,
                amount="1.00",
                currency="USD",
                observed_at=SEEN_AT,
                confidence=0.5,
            )
            for identifier in external_ids
        )


def test_a_source_answers_a_batch_with_what_the_provider_said() -> None:
    source: QuoteSource = _Source()

    quotes = asyncio.run(source.fetch(("a", "b")))

    assert source.provider == "examplesource"
    assert [quote.external_id for quote in quotes] == ["a", "b"]


def test_an_outage_is_the_ports_own_error() -> None:
    source: QuoteSource = _Source(down=True)

    with pytest.raises(MarketProviderUnavailable):
        asyncio.run(source.fetch(("a",)))
