"""record market ingestion runs

Issue #54 runs market ingestion once a day, and a run that leaves no record is
one whose partial failure or dropped coverage nobody can see. One row per run:
where it is, what it stored, what it quarantined, which snapshot it cut, and why
it failed if it did.

Not append-only, unlike its siblings: the row is the run's progress, moving from
`running` to an outcome, and never market data.

A partial unique index admits one `running` row at a time. #51's snapshot
cut-line is sound only while there is one ingestion writer, and this makes that
a database guarantee rather than a property of the schedule.

The shape and the reasoning live in `services/api/src/tcg_api/market/tables.py`.
The vocabularies are written out as literals, because a migration is a snapshot
of what was applied; `test_market_tables.py` checks the two still agree.

Refs: M4, #54, spec §37, §67

Revision ID: 9b3f6d2e8a41
Revises: 7e2c4a9b1d63
Create Date: 2026-09-15 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9b3f6d2e8a41"
down_revision: str | None = "7e2c4a9b1d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = "'running', 'completed', 'failed', 'skipped'"
FAILURE_REASONS = "'abandoned', 'provider_unavailable', 'store_unreachable'"


def upgrade() -> None:
    op.create_table(
        "market_ingestion_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider_id",
            sa.Uuid(),
            nullable=True,
            comment="The provider ingested from. NULL only for a `skipped` run, which had none.",
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "failure_reason",
            sa.Text(),
            nullable=True,
            comment="Why a `failed` run failed, as a code. NULL for every other status.",
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "batches",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="Batches of provider identifiers the run asked for.",
        ),
        sa.Column(
            "batches_failed",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment=(
                "Batches the provider did not answer after every retry. "
                "A partial run is still a run."
            ),
        ),
        sa.Column("stored", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "quarantined",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Quarantined quotes by `QuarantineReason`. Absent reasons are absent, not zero.",
        ),
        sa.Column(
            "unmapped",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment=(
                "Provider identifiers no catalog card carries. "
                "Counts only; the identifiers are in `market_quarantine`."
            ),
        ),
        sa.Column(
            "snapshot_id",
            sa.Uuid(),
            nullable=True,
            comment=(
                "The snapshot a `completed` run cut, "
                "in the transaction that wrote its observations."
            ),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_ingestion_runs"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["market_providers.id"],
            name="fk_market_ingestion_runs_provider_id_market_providers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["market_snapshots.id"],
            name="fk_market_ingestion_runs_snapshot_id_market_snapshots",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(f"status IN ({RUN_STATUSES})", name="status_is_a_run_status"),
        sa.CheckConstraint(
            f"failure_reason IN ({FAILURE_REASONS})", name="failure_reason_is_a_run_failure"
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)", name="failed_runs_name_a_reason"
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (snapshot_id IS NOT NULL)",
            name="completed_runs_name_a_snapshot",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (completed_at IS NULL)",
            name="only_running_runs_are_unfinished",
        ),
        sa.CheckConstraint(
            "status = 'skipped' OR provider_id IS NOT NULL",
            name="only_skipped_runs_have_no_provider",
        ),
        sa.CheckConstraint(
            "batches >= 0 AND batches_failed >= 0 AND stored >= 0 AND unmapped >= 0",
            name="counts_are_not_negative",
        ),
        comment=(
            "One scheduled market ingestion run and what it did — issue #54. Not append-only: "
            "a row moves from `running` to its outcome. The coverage alert reads the counts."
        ),
    )
    op.create_index(
        "uq_market_ingestion_runs_one_running",
        "market_ingestion_runs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("uq_market_ingestion_runs_one_running", table_name="market_ingestion_runs")
    op.drop_table("market_ingestion_runs")
