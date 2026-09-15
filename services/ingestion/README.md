# `services/ingestion`

Scheduled ingestion of external market data into `market_observations` and the
immutable `market_snapshots` that analyses reference.

**The run lives in `services/api`, not here** (#54). It is
`tcg_api.market.ingestion`, scheduled daily as `tcg_api.market.ingest` by the
worker's embedded beat, because it reuses the API's normalization, snapshots
and hardened Celery configuration and runs in the same worker image. A package
here would have to import `tcg_api`, which is the dependency the wrong way round.
The provider it reads is a `tcg_market_data.QuoteSource`; #52 supplies one.
