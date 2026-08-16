# `services/market-data`

Serves pre-ingested market snapshots to the rest of the system.

**Never calls an external provider during a user request.** Reads only what
`services/ingestion` has already written. Distribution name is
`tcg-market-data-service` to avoid colliding with the `tcg-market-data` package.

Populated in M4.
