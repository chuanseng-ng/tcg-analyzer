"""record exchange rates and quarantined market records

Issue #53 normalizes a provider's quotes before they reach the database, and the
schema was missing three things it needs.

* **`exchange_rates`.** ADR 0006's provider prices in USD and V1 reports SGD.
  No FX feed exists, and one would be a new external provider needing its own
  ADR, so a rate is recorded by an operator (`tcg-record-exchange-rate`) with
  the source it was read from. Append-only: a rewritten rate would silently
  reprice every observation that names it.
* **`market_observations.price_sgd` and `exchange_rate_id`.** `price` and
  `currency` keep what the provider said, as #50's column comment promised; the
  converted figure sits beside them with the rate that produced it. Without the
  rate a historical snapshot cannot be reproduced, which is §36's whole purpose.
  `price_sgd` is NOT NULL with no default: nothing has ever been ingested,
  because no `market_providers` row exists, so there is no row to backfill — and
  a backfill would be an UPDATE, which the append-only trigger refuses anyway.
* **`market_quarantine`.** A rejected record is kept with a reason rather than
  dropped, because a silent drop makes a coverage gap indistinguishable from a
  bug. Append-only; DELETE stays open for pruning, like its siblings.

Both new triggers reuse `market_rows_are_immutable()`, created by the
market-data revision, so `downgrade` must not drop the function.

The shape and the reasoning live in `services/api/src/tcg_api/market/tables.py`.
The vocabularies are written out as literals, because a migration is a snapshot
of what was applied; `test_market_tables.py` checks the two still agree.

Refs: M4, #53, spec §35, §36, §37, ADR 0006

Revision ID: 7e2c4a9b1d63
Revises: d5a91c47f6b2
Create Date: 2026-09-15 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7e2c4a9b1d63"
down_revision: str | None = "d5a91c47f6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENCY_PATTERN = "^[A-Z]{3}$"
QUARANTINE_REASONS = "'unmapped_card', 'ambiguous_card', 'missing_field', 'invalid_price', 'price_out_of_bounds', 'invalid_confidence', 'invalid_currency', 'no_exchange_rate', 'unsupported_company', 'invalid_grade', 'unsupported_grade', 'implausible_observed_at'"

EXCHANGE_RATES_TRIGGER = """
CREATE TRIGGER trg_exchange_rates_immutable
BEFORE UPDATE ON exchange_rates
FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
"""

QUARANTINE_TRIGGER = """
CREATE TRIGGER trg_market_quarantine_immutable
BEFORE UPDATE ON market_quarantine
FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
"""

CURRENCY_COMMENT = (
    "The ISO 4217 code the provider quoted in, as it said it. `price` is in this "
    "currency; `price_sgd` is the normalized figure, and `exchange_rate_id` names "
    "the rate between them. Not COLLATE C: compared for equality only."
)

PREVIOUS_CURRENCY_COMMENT = (
    "The ISO 4217 code the provider quoted in. V1 reports SGD and converts "
    "nothing, but the selected provider prices in USD — an observation records "
    "what was said, and normalization owns the conversion. Not COLLATE C: "
    "compared for equality only."
)

PRICE_SGD_COMMENT = (
    "The price in SGD, which is what snapshots resolve and the economic engine "
    "reads (spec §46). Equal to `price` for an SGD quote; otherwise `price` "
    "times the named rate, rounded half away from zero as `Money` rounds. Issue #53."
)

EXCHANGE_RATE_ID_COMMENT = (
    "The rate that converted `price` into `price_sgd`. NULL exactly when the "
    "quote was already SGD. RESTRICT: a rate an observation used must stay "
    "resolvable, or the SGD figure could not be reproduced."
)


def upgrade() -> None:
    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "base_currency",
            sa.Text(),
            nullable=False,
            comment="The ISO 4217 code converted from.",
        ),
        sa.Column(
            "quote_currency",
            sa.Text(),
            nullable=False,
            comment="Always SGD in V1: the currency every stored price is normalized to.",
        ),
        sa.Column(
            "rate",
            sa.Numeric(18, 8),
            nullable=False,
            comment=(
                "Units of `quote_currency` per one `base_currency`, exactly. Never "
                "floating point: it multiplies a price."
            ),
        ),
        sa.Column(
            "as_of",
            sa.Date(),
            nullable=False,
            comment="The day the rate applies to.",
        ),
        sa.Column(
            "source_reference",
            sa.Text(),
            nullable=False,
            comment=(
                "Where the operator read the rate, as a URL a human can open. Never "
                "empty: a rate with no source is a number someone typed."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the rate was recorded, as distinct from the day it applies to.",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exchange_rates"),
        sa.UniqueConstraint(
            "base_currency",
            "quote_currency",
            "as_of",
            name="uq_exchange_rates_base_currency_quote_currency_as_of",
        ),
        sa.CheckConstraint(
            f"base_currency ~ '{CURRENCY_PATTERN}'", name="base_currency_is_an_iso_4217_code"
        ),
        sa.CheckConstraint("quote_currency = 'SGD'", name="quote_currency_is_sgd"),
        sa.CheckConstraint("rate > 0", name="rate_is_positive"),
        comment=(
            "One operator-recorded exchange rate into SGD — issue #53. Append-only, "
            "enforced by trg_exchange_rates_immutable: a rewritten rate would silently "
            "reprice every observation that names it."
        ),
    )
    op.execute(EXCHANGE_RATES_TRIGGER)

    op.add_column(
        "market_observations",
        sa.Column("price_sgd", sa.Numeric(12, 2), nullable=False, comment=PRICE_SGD_COMMENT),
    )
    op.add_column(
        "market_observations",
        sa.Column("exchange_rate_id", sa.Uuid(), nullable=True, comment=EXCHANGE_RATE_ID_COMMENT),
    )
    op.create_foreign_key(
        "fk_market_observations_exchange_rate_id_exchange_rates",
        "market_observations",
        "exchange_rates",
        ["exchange_rate_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint("price_sgd_is_not_negative", "market_observations", "price_sgd >= 0")
    op.create_check_constraint(
        "foreign_prices_name_their_exchange_rate",
        "market_observations",
        "(currency = 'SGD') = (exchange_rate_id IS NULL)",
    )
    op.create_check_constraint(
        "sgd_prices_are_not_converted",
        "market_observations",
        "currency <> 'SGD' OR price_sgd = price",
    )
    op.alter_column(
        "market_observations",
        "currency",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=CURRENCY_COMMENT,
        existing_comment=PREVIOUS_CURRENCY_COMMENT,
    )

    op.create_table(
        "market_quarantine",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            nullable=False,
            comment="Which provider sent the record, and so under which licence it is held.",
        ),
        sa.Column(
            "external_id",
            sa.Text(),
            nullable=True,
            comment="The provider's identifier for the card, verbatim, if it sent one.",
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
            comment=(
                "Why the record was not stored — `tcg_market_data.QuarantineReason`. "
                "`unmapped_card` is a catalog problem worth reporting, not something to "
                "invent a card for."
            ),
        ),
        sa.Column(
            "detail",
            sa.Text(),
            nullable=False,
            comment="The specific failure, in words a human reviewing the run can act on.",
        ),
        sa.Column(
            "record",
            postgresql.JSONB(),
            nullable=False,
            comment=(
                "The quote as the provider said it, with amounts and timestamps as text so "
                "nothing is rounded or re-zoned on the way in."
            ),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_quarantine"),
        sa.CheckConstraint(
            f"reason IN ({QUARANTINE_REASONS})", name="reason_is_a_quarantine_reason"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["market_providers.id"],
            name="fk_market_quarantine_provider_id_market_providers",
            ondelete="RESTRICT",
        ),
        comment=(
            "A provider record normalization refused, kept with its reason rather than "
            "dropped — issue #53. Append-only, enforced by "
            "trg_market_quarantine_immutable; DELETE stays open for pruning."
        ),
    )
    op.create_index(
        "ix_market_quarantine_provider_id_created_at",
        "market_quarantine",
        ["provider_id", "created_at"],
    )
    op.execute(QUARANTINE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_market_quarantine_immutable ON market_quarantine")
    op.drop_index("ix_market_quarantine_provider_id_created_at", table_name="market_quarantine")
    op.drop_table("market_quarantine")

    op.alter_column(
        "market_observations",
        "currency",
        existing_type=sa.Text(),
        existing_nullable=False,
        comment=PREVIOUS_CURRENCY_COMMENT,
        existing_comment=CURRENCY_COMMENT,
    )
    op.drop_constraint("sgd_prices_are_not_converted", "market_observations", type_="check")
    op.drop_constraint(
        "foreign_prices_name_their_exchange_rate", "market_observations", type_="check"
    )
    op.drop_constraint("price_sgd_is_not_negative", "market_observations", type_="check")
    op.drop_constraint(
        "fk_market_observations_exchange_rate_id_exchange_rates",
        "market_observations",
        type_="foreignkey",
    )
    op.drop_column("market_observations", "exchange_rate_id")
    op.drop_column("market_observations", "price_sgd")

    op.execute("DROP TRIGGER IF EXISTS trg_exchange_rates_immutable ON exchange_rates")
    op.drop_table("exchange_rates")
    # `market_rows_are_immutable()` is deliberately left standing: the market-data
    # revision created it and three other tables' triggers still call it.
