"""record a consent to keep a photograph

ADR 0008 approves this product's own user uploads as training-image source
class 4 *where the user consented*, and until now nothing asked, so the class
supplied nothing. Issue #148 is the consent, and this column is the only piece
of it the schema was missing: everything else was declared for class 4 in
advance — `physical_copy_id` is already nullable for it, `source_reference`
already documents itself as a consented upload's analysis identifier, and
`("product_upload", "uploaded_by_user_with_consent")` is already in
`APPROVED_SOURCES`.

Spec §54 deletes the session that produced the photograph, deliberately, so
that a per-browser identifier is not kept forever — which leaves a consented
image with nothing to be reached through. The answer is the one #270 used for
the same problem: a bearer capability minted at the moment of consent, shown
once, and stored only as a sha256. `tcg_api.codes` renders upper-case Crockford
base32 and this column's CHECK admits lowercase hex, so the two alphabets are
disjoint and a code cannot be written here even by mistake.

Nullable, and NULL on every other source: classes 1 and 2 are photographs this
project took, and class 3 withdraws through the grant reference already in
`source_reference`. Not unique — one consent covers the front and the back of
one card — so the index is a plain partial one over the rows that carry a
value.

The retention exemption this makes possible is justified in `docs/retention.md`,
which was written before this revision existed.

Refs: M10, M6, spec §29, §53, §54, #148, ADR 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a91c47f6b2"
down_revision: str | None = "b3f18c7d94ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Redeclared locally, as every migration in this history does: a revision is a
# snapshot of what was applied and must not change when a table module does.
PRINTED = sa.Text(collation="C")
SHA256_PATTERN = "^[0-9a-f]{64}$"
WITHDRAWAL_CODE_IS_A_DIGEST = (
    f"withdrawal_code_hash IS NULL OR withdrawal_code_hash ~ '{SHA256_PATTERN}'"
)

_WITHDRAWAL_CODE_HASH_COMMENT = (
    "The sha256 of the code shown once to a user who consented to this "
    "photograph being kept — issue #148, ADR 0008's approved class 4. NULL on "
    "every other source: classes 1 and 2 are ours, and class 3 withdraws "
    "through the grant reference in `source_reference`. It is the only way "
    "back to this row, because spec §54 deletes the session that produced it."
)


def upgrade() -> None:
    op.add_column(
        "training_images",
        sa.Column(
            "withdrawal_code_hash",
            PRINTED,
            nullable=True,
            comment=_WITHDRAWAL_CODE_HASH_COMMENT,
        ),
    )
    # The short names, not the rendered ones — in `downgrade` as well. Alembic
    # applies `target_metadata`'s naming convention itself.
    op.create_check_constraint(
        "withdrawal_code_is_stored_as_a_digest",
        "training_images",
        WITHDRAWAL_CODE_IS_A_DIGEST,
    )
    op.create_index(
        "ix_training_images_withdrawal_code_hash",
        "training_images",
        ["withdrawal_code_hash"],
        postgresql_where=sa.text("withdrawal_code_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_training_images_withdrawal_code_hash", table_name="training_images")
    op.drop_constraint("withdrawal_code_is_stored_as_a_digest", "training_images", type_="check")
    op.drop_column("training_images", "withdrawal_code_hash")
