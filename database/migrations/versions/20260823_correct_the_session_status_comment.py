"""correct what analysis_sessions.status says the retention sweep does

The column's comment has promised, since the analysis schema was created, that
a session becomes `purged` "once the retention job has deleted the images and
kept the row so the deletion itself is auditable". #41 builds that job and does
not do that.

It deletes the session. `analysis_sessions.anonymous_session_id` is the
browser's bearer token, so a row kept to record that its images were deleted is
a per-browser identifier kept forever — which is what spec §53's "do not
permanently tie analyses to personal identity" and §54's "expire unless retained
for an explicitly justified purpose" both argue against. The deletion is audited
by the `retention.swept` log line, which carries counts and nothing that says
whose photographs they were.

So `active` is the only value written, and the comment now says so. `expired`
and `purged` stay in the CHECK: they are states this schema admits, and removing
a value would cost a migration against a table of user data for no behaviour.

Comment-only. Alembic's autogenerate compares column comments — which is how
this revision came to exist rather than the comment quietly drifting from
`tables.py` — and `COMMENT ON` takes no lock worth naming.

Refs: M2, spec §53, §54
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1f6a2c8b40de"
down_revision: str | None = "7e4a90c15db3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT = (
    "active, expired, or purged. Spec §12 names this column and never "
    "says what may go in it; these three are this project's, not the "
    "specification's — and only active is written, because #41's "
    "retention sweep deletes an expired session rather than emptying it."
)

PREVIOUS = (
    "active, expired, or purged once the retention job has deleted the images and "
    "kept the row so the deletion itself is auditable. Spec §12 names this column "
    "and never says what may go in it; these three are this project's, not the "
    "specification's."
)


def upgrade() -> None:
    op.alter_column(
        "analysis_sessions",
        "status",
        comment=CURRENT,
        existing_comment=PREVIOUS,
        existing_type=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "analysis_sessions",
        "status",
        comment=PREVIOUS,
        existing_comment=CURRENT,
        existing_type=sa.Text(),
        existing_nullable=False,
    )
