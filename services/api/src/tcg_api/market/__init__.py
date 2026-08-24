"""Market-data storage — §35's providers and prices, and §36's snapshots of them.

`packages/market-data` is where the market domain *is*: §33's
`MarketDataProvider` port, and `PriceObservation`, which validates one price in
memory. This package is the database side of it. `tables.py` declares §35's
`market_providers` and `market_observations` and §36's `market_snapshots`, and
`snapshots.py` is the only place a snapshot is generated or resolved — the
provider adapter (#52), normalization (#53) and the ingestion worker (#54) all
write here rather than declaring storage of their own.

Nothing here names a provider. `market_providers` is a table of rows, not a
vocabulary, so adding a second source costs an INSERT.
"""
