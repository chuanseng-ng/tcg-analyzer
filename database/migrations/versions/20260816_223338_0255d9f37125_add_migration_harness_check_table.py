"""add migration harness check table

The baseline revision. It carries no domain meaning: it exists so that
`upgrade` -> `downgrade` -> `upgrade` proves the harness works against a real
PostgreSQL on a fresh volume, and so the first domain migration has a parent to
revise. Domain tables (cards, analyses, images, market_observations) arrive in
their own milestones.

Plain-PostgreSQL DDL only — no extensions, nothing Supabase-specific. Spec §8
requires the domain stay portable to ordinary PostgreSQL, and that option only
survives if the schema history never depends on a platform feature.

Revision ID: 0255d9f37125
Revises:
Create Date: 2026-08-16 22:33:38.357437+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0255d9f37125"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "migration_harness_check"

TABLE_COMMENT = (
    "Scaffolding, not domain. This table exists only to prove the Alembic "
    "migration harness applies and reverts cleanly end to end (M0, #15). It "
    "carries no data and will be dropped by the migration that introduces the "
    "first domain table."
)


def upgrade() -> None:
    # `comment=` emits `COMMENT ON TABLE ... IS '...'` as a separate statement
    # with the literal correctly quoted. Writing that SQL by hand is not an
    # option: COMMENT is DDL, so PostgreSQL rejects a bind parameter there, and
    # the alternative is hand-escaping quotes into an f-string.
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        comment=TABLE_COMMENT,
    )


def downgrade() -> None:
    op.drop_table(TABLE_NAME)
