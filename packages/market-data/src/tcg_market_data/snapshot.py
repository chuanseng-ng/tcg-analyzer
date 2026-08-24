"""Spec §36's market snapshot: the prices one analysis was computed against.

A snapshot is **a moment, not a copy**. §36 draws it with `observations` hanging
off it, and the naive reading is a membership table — one row per price per
snapshot, tens of thousands a day. It is not needed: prices are append-only, and
a snapshot is fully determined by which provider it read and when it was cut, so
"the observations that had landed by then" can never change afterwards. That is
what makes a historical analysis re-derivable rather than re-guessed, and it is
why this record holds four fields and no list.

`services/api`'s `tcg_api.market.snapshots` is where one is generated and
resolved; only the record lives here. That split follows
:class:`tcg_grading_companies.GradingRules` rather than
`tcg_api.analysis.sessions.AnalysisRecord`: a snapshot is a structure §36 names
in the specification, so it belongs to the domain the specification gives it to,
and constructing this frozen dataclass is the validation on the way out of the
database. It is deliberately **not** part of the
:class:`~tcg_market_data.port.MarketDataProvider` port — a provider is asked for
prices, never for a snapshot, because §37 forbids calling one on the read path
at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from tcg_market_data.errors import InvalidMarketSnapshot

__all__ = ["MarketSnapshot"]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One immutable snapshot — spec §36's four fields.

    Args:
        id: The snapshot's identifier. This is what `analyses.market_snapshot_id`
            records, so an analysis can resolve the exact prices it used however
            many ingestion runs have happened since.
        provider: §36's `provider`, as the identifier of the `market_providers`
            row rather than its slug. A snapshot reads one provider: two
            providers' figures for one card are two answers, and a snapshot that
            mixed them would be reproducible from neither licence.
        generated_at: When the snapshot was cut, and **the cut itself** — the
            snapshot comprises the observations stored at or before this moment.
            Timezone-aware, like every other instant in this package: a naive one
            would silently move the cut by the reader's offset.
        data_version: This repository's identifier for the ingestion run behind
            it — never a `/latest/` pointer (spec §31), and never the provider's
            own version, which is `market_providers.version` and which no
            candidate M3 surveyed publishes at all. ADR 0006 therefore has it
            hold the ingestion date, in UTC. The database generates it from
            `generated_at`, so the two cannot disagree; it is the date stamp ADR
            0006 requires the results UI to show beside a price.
    """

    id: UUID
    provider: UUID
    generated_at: datetime
    data_version: date

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise InvalidMarketSnapshot(f"id must be a UUID, got {type(self.id).__name__}")
        if not isinstance(self.provider, UUID):
            raise InvalidMarketSnapshot(
                f"provider must be a market_providers UUID, got {type(self.provider).__name__}"
            )
        if not isinstance(self.generated_at, datetime) or self.generated_at.utcoffset() is None:
            raise InvalidMarketSnapshot(
                f"generated_at must be a timezone-aware datetime, got {self.generated_at!r}"
            )
        # `date` is a `datetime` subclass, so the order of these two checks
        # matters: a `datetime` handed in as `data_version` would otherwise pass.
        if isinstance(self.data_version, datetime) or not isinstance(self.data_version, date):
            raise InvalidMarketSnapshot(
                f"data_version must be a date, got {type(self.data_version).__name__}"
            )

    def __str__(self) -> str:
        return f"{self.data_version.isoformat()} @ {self.generated_at.isoformat()}"
