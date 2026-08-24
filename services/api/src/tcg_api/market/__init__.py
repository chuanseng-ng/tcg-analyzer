"""Market-data storage — spec §35's providers and the prices they reported.

`packages/market-data` is where the market domain *is*: §33's
`MarketDataProvider` port, and `PriceObservation`, which validates one price in
memory. This package is the database side of it. `tables.py` declares §35's
`market_providers` and `market_observations`, and nothing else exists yet — the
provider adapter (#52), normalization (#53), the ingestion worker (#54) and
snapshots (#51) all write here rather than declaring storage of their own.

Nothing here names a provider. `market_providers` is a table of rows, not a
vocabulary, so adding a second source costs an INSERT.
"""
