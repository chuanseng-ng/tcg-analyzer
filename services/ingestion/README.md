# `services/ingestion`

Scheduled ingestion of external market data into `market_observations` and the
immutable `market_snapshots` that analyses reference.

Runs on a schedule, out of band from user requests. Handles provider failure,
rate limits and caching rights.

Populated in M4.
