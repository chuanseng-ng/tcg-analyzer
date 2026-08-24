"""add the market snapshot schema

Spec §36 requires every analysis to use a market snapshot, so a historical
analysis can be re-derived with the exact prices it used rather than with
whatever is current when somebody re-reads it. `analyses.market_snapshot_id` has
carried no foreign key since the reproducibility record landed, because the
table it points at did not exist; this revision creates it and adds the key.

The shape and the reasoning live in
`services/api/src/tcg_api/market/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Four things worth knowing before reading the DDL:

* **A snapshot stores no observations.** §36 draws them hanging off it, but the
  set is already determined: `market_observations` is append-only and its
  `created_at` records when a row *landed*, so "this provider's observations
  whose `created_at` is at or before `generated_at`" can never change after the
  fact. A membership table would be tens of thousands of rows a day carrying no
  information, and a late-arriving backfill still cannot join a snapshot cut
  before it arrived.
* **`generated_at` is the cut-line, not merely a timestamp.** One column rather
  than a separate cutoff, because two columns that must agree is the drift
  `market_type` was generated to avoid.
* **`data_version` is generated, not written.** ADR 0006 fixes its content —
  no provider publishes a version, so it holds the ingestion date — and a run
  that could name its own version could name one that disagreed with when it was
  cut. Generating it from `generated_at` makes that unrepresentable, exactly as
  `market_type` is generated from `grading_company`.
* **`analyses.market_snapshot_id` gets RESTRICT**, where `card_database_version`
  carries no key at all. A catalog version is an identifier worth keeping even
  if the record went; a snapshot *is* the prices, so one an analysis references
  must stay resolvable forever.

The append-only trigger reuses `market_rows_are_immutable()`, created by the
market-data revision — it names its table with `TG_TABLE_NAME`, so a third table
costs one CREATE TRIGGER and no second copy of the function. `downgrade` must
therefore *not* drop the function: the other two tables still call it.

No rows are inserted here. There is no `market_providers` row to generate a
snapshot for, and ADR 0006 gates that on a subscription that is not yet active.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_market_schema.py` asserts an
UPDATE is actually refused; that test is the only guard there is.

Revision ID: 3ac71e5d92f8
Revises: b5bca50f46c0
Create Date: 2026-08-24 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3ac71e5d92f8"
down_revision: str | None = "b5bca50f46c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Written out as a literal rather than imported from the table module. A
# migration is a snapshot of what was applied; `test_market_tables.py` checks
# that the two still agree.
DATA_VERSION_EXPRESSION = "(generated_at AT TIME ZONE 'UTC')::date"

# Kept identical to the market-data revision's copy so the two can be diffed by
# eye; see that file for why `RAISE USING MESSAGE = ...` is concatenated.
SNAPSHOTS_TRIGGER = """
CREATE TRIGGER trg_market_snapshots_immutable
BEFORE UPDATE ON market_snapshots
FOR EACH ROW EXECUTE FUNCTION market_rows_are_immutable();
"""

SNAPSHOT_COMMENT = (
    "Which pre-ingested market snapshot the economics were computed against — "
    "spec §36, resolved when the run claimed the analysis. RESTRICT, unlike "
    "`card_database_version` which carries no key at all: a catalog version is "
    "an identifier worth keeping even if the record went, where a snapshot "
    "*is* the prices, and one an analysis references must stay resolvable "
    "forever. NULL until something has ingested — a fact rather than a gap."
)

PREVIOUS_SNAPSHOT_COMMENT = (
    "Which pre-ingested market snapshot the economics were computed "
    "against. No foreign key yet — the table it will point at arrives with "
    "the market-data milestone, and adding the constraint then is cheaper "
    "than changing this column's type."
)

CONFIGURATION_COMMENT = (
    "The fee and cost configuration used. No foreign key yet — the table it "
    "will point at arrives with the economics milestone, and adding the "
    "constraint then is cheaper than changing this column's type."
)

PREVIOUS_CONFIGURATION_COMMENT = (
    "The fee and cost configuration used. No foreign key yet, as above."
)


def upgrade() -> None:
    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "§36's `provider`, as the identifier rather than the slug. A snapshot "
                "reads one provider: two providers' figures for one card are two answers, "
                "and a snapshot that mixed them would be reproducible from neither licence."
            ),
        ),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment=(
                "§36's `generated_at`, and **the cut-line itself**: this snapshot comprises "
                "its provider's observations whose `created_at` is at or before it. Written "
                "by the database, never by a caller, so a cut cannot be backdated past "
                "prices that had already landed. The comparison is `<=` rather than `<`, "
                "and that is load-bearing: a snapshot is generated inside the transaction "
                "that wrote the run's observations, and `now()` is transaction-start time, "
                "so those rows carry exactly this value. With `<` a run would snapshot the "
                "day before its own work."
            ),
        ),
        sa.Column(
            "data_version",
            sa.Date(),
            sa.Computed(DATA_VERSION_EXPRESSION, persisted=True),
            nullable=False,
            comment=(
                "§36's `data_version` — **this repository's** identifier for the ingestion "
                "run, never a pointer (spec §31), and never the provider's own version, "
                "which is `market_providers.version` and which no candidate M3 surveyed "
                "publishes at all. ADR 0006 therefore has it hold the ingestion date, so "
                "it is generated from `generated_at` rather than written: a run that could "
                "name its own version could name one that disagreed with when it was cut. "
                "UTC, so the stamp does not move with the server's timezone."
            ),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_snapshots"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["market_providers.id"],
            name="fk_market_snapshots_provider_id_market_providers",
            ondelete="RESTRICT",
        ),
        comment=(
            "One immutable market snapshot — spec §36. It stores no observations: the "
            "snapshot *is* `generated_at`, and it comprises the observations from its "
            "provider whose `created_at` is at or before that moment. Because "
            "`market_observations` is append-only, that set can never change afterwards, "
            "which is what makes a historical analysis re-derivable rather than "
            "re-guessed. Append-only itself, enforced by trg_market_snapshots_immutable."
        ),
    )

    op.execute(SNAPSHOTS_TRIGGER)

    op.create_foreign_key(
        "fk_analyses_market_snapshot_id_market_snapshots",
        "analyses",
        "market_snapshots",
        ["market_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Alembic compares column comments, so a reworded comment in `tables.py` and
    # not here is a drift the guard fails on.
    op.alter_column(
        "analyses",
        "market_snapshot_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        comment=SNAPSHOT_COMMENT,
        existing_comment=PREVIOUS_SNAPSHOT_COMMENT,
    )
    # Its neighbour said "as above" of a sentence that no longer reads that way.
    op.alter_column(
        "analyses",
        "economic_configuration_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        comment=CONFIGURATION_COMMENT,
        existing_comment=PREVIOUS_CONFIGURATION_COMMENT,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "economic_configuration_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        comment=PREVIOUS_CONFIGURATION_COMMENT,
        existing_comment=CONFIGURATION_COMMENT,
    )
    op.alter_column(
        "analyses",
        "market_snapshot_id",
        existing_type=sa.Uuid(),
        existing_nullable=True,
        comment=PREVIOUS_SNAPSHOT_COMMENT,
        existing_comment=SNAPSHOT_COMMENT,
    )
    op.drop_constraint(
        "fk_analyses_market_snapshot_id_market_snapshots",
        "analyses",
        type_="foreignkey",
    )
    op.execute("DROP TRIGGER IF EXISTS trg_market_snapshots_immutable ON market_snapshots")
    op.drop_table("market_snapshots")
    # `market_rows_are_immutable()` is deliberately left standing: the market-data
    # revision created it and its other two triggers still call it.
