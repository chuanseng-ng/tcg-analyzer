"""Economic configuration storage — spec §45, §46 and §57's `economic_configuration`.

`packages/economic-engine` is where the economics *are*: §40's expectation,
§41's two profit figures, §42's two ratios, §43's five modes and §44's
recommendation. This package is the database side of it, and nothing more —
`tables.py` declares `economic_configurations` and `store.py` is the only place
one is written or read.

**Nothing here calculates anything.** The master architectural rule keeps
grading separate from economics; this package keeps *storage* separate from
both. What it stores is the engine's own frozen types, built on the way out, so
a caller never re-parses a `Decimal` that has already been validated once.

A configuration is written once and never updated: re-running with different
costs is a new analysis, which is what makes §57's record mean something.
"""
