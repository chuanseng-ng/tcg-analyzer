"""The `MarketDataProvider` port (spec §33) and an in-memory implementation.

Spec §2.4: "no marketplace, price provider, card database, or external API may
become a hard dependency of the core domain". This package is that interface for
market prices. ADR 0006 selects PokePriceTracker for V1 and says the same thing
from the other side — the vendor "enters the system behind §33's
`MarketDataProvider` and nothing more" — so #52's adapter will live in its own
module and, like :mod:`tcg_shared.storage.s3`, **will not be re-exported here**.
Importing ``tcg_market_data`` must pull in nothing outside the standard library
and this project's own stdlib-only packages; reach for an adapter explicitly
when you mean to bind to one. ``tests/test_market_data_purity.py`` enforces it.

The package depends on `tcg-domain` and `tcg-grading-companies` and nothing
else. It reaches no network: the port says what may be asked, and the only
implementation here answers from a list.

Everything re-exported below is the package's public surface; nothing else is.
"""

from __future__ import annotations

from tcg_market_data.errors import (
    InvalidMarketObservation,
    InvalidMarketSnapshot,
    MarketDataError,
    MarketProviderUnavailable,
)
from tcg_market_data.freshness import (
    FRESH_WITHIN,
    STALE_FLOOR,
    price_age,
    price_confidence,
)
from tcg_market_data.memory import InMemoryMarketDataProvider
from tcg_market_data.port import (
    MarketDataProvider,
    MarketType,
    PriceObservation,
    validated_grade_key,
)
from tcg_market_data.snapshot import MarketSnapshot

__all__ = [
    "FRESH_WITHIN",
    "STALE_FLOOR",
    "InMemoryMarketDataProvider",
    "InvalidMarketObservation",
    "InvalidMarketSnapshot",
    "MarketDataError",
    "MarketDataProvider",
    "MarketProviderUnavailable",
    "MarketSnapshot",
    "MarketType",
    "PriceObservation",
    "price_age",
    "price_confidence",
    "validated_grade_key",
]
