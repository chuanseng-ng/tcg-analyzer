"""add the training image fingerprints

Spec §28 puts deduplication between image validation and annotation, and §32
forbids splitting near-identical photographs of one card across train and test.
#153 gave the exact half of that for free — `uq_training_images_sha256` makes a
second ingest of identical bytes unrepresentable — and this revision adds the
near half: one perceptual fingerprint per training image, taken over the
standardized artifact rather than over the photograph.

The shape and the reasoning live in
`services/api/src/tcg_api/datasets/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Five things worth knowing before reading the DDL:

* **There is no pairs table and no groups table, deliberately.** A duplicate
  relationship is a pure function of two hashes and a threshold, and the
  threshold is not stored — so it is computed when asked, exactly as
  `market_snapshots` derives its membership from a cut-line rather than listing
  it. A stored pair is a second answer that drifts from the first the moment the
  threshold moves, and re-deriving it is a popcount.
* **Both hash columns are nullable, and a NULL pair is a record rather than a
  gap.** An image the detector finds no card in yields no artifact and therefore
  no hash. Writing the row anyway, under the `hash_version` that examined it, is
  what stops the next pass decoding those bytes all over again; a detector
  version bump retries it because the version no longer matches.
* **The foreign key CASCADEs, where every other key into `training_images` is
  RESTRICT.** `dataset_members` restricts because §31 means a version cannot
  un-include an image. A hash means nothing without the bytes it describes, so it
  must never be the reason a row cannot be removed.
* **The foreign key's name is shortened away from the convention.** The
  convention renders `fk_training_image_fingerprints_training_image_id_
  training_images`, which is 64 bytes — one over PostgreSQL's limit. The two
  shortened names on `physical_copies` are the precedent and the failure is the
  same: a truncated name reflects back differently from the declared one, and
  `--autogenerate` then reports a drop-and-re-add for ever.
* **No trigger and no index.** Recomputing a fingerprint under a new
  `hash_version` is an `UPDATE`, so the immutability trigger `dataset_versions`
  and `dataset_members` carry would be wrong here — `physical_copies` and
  `training_images` are the precedent. And the pass reads this table whole, joins
  it by primary key and then compares every pair against every other, none of
  which an index helps.

No rows are inserted. `downgrade()` drops the table and **nothing else**: this
revision creates no function, and dropping `dataset_records_are_immutable()`
here would unguard the two tables that still call it.

Revision ID: a809e54401d2
Revises: 6f49252e81d4
Create Date: 2026-08-29 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a809e54401d2"
down_revision: str | None = "6f49252e81d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

# A 64-bit difference hash rendered as 16 lowercase hex characters, the spelling
# `SHA256_PATTERN` uses in the revision below this one. Written out as a literal
# rather than built from the table module: a migration is a snapshot of what was
# applied, and `test_datasets_tables.py` checks that the two still agree.
PERCEPTUAL_HASH_PATTERN = "^[0-9a-f]{16}$"

# Both hash columns share one grammar, so they share one CHECK — composed from
# the pattern above so the width is stated once. Each disjunct admits NULL,
# because a row that records "no card was located here" carries neither hash and
# must still be storable; `both_hashes_or_neither` is what keeps that honest.
HASHES_ARE_HEX = (
    f"(perceptual_hash IS NULL OR perceptual_hash ~ '{PERCEPTUAL_HASH_PATTERN}') "
    f"AND (perceptual_hash_rotated IS NULL "
    f"OR perceptual_hash_rotated ~ '{PERCEPTUAL_HASH_PATTERN}')"
)


def upgrade() -> None:
    op.create_table(
        "training_image_fingerprints",
        sa.Column(
            "training_image_id",
            sa.Uuid(),
            nullable=False,
            comment=(
                "The image this fingerprint was taken over, and this table's whole identity "
                "— a derived row has nothing else to be keyed on. **CASCADE, where every "
                "other key into `training_images` is RESTRICT**: `dataset_members` restricts "
                "because §31 means a version cannot un-include an image, but a hash means "
                "nothing without the bytes it describes, so it must never be the reason a "
                "row cannot be removed."
            ),
        ),
        sa.Column(
            "perceptual_hash",
            PRINTED,
            nullable=True,
            comment=(
                "A 64-bit difference hash over the **normalized artifact**, as 16 lowercase "
                "hex characters. Hashing the 756x1056 artifact rather than the photograph "
                "takes framing and perspective out of the comparison for free. NULL when no "
                "card could be located in the bytes: that is an answer rather than a gap — "
                "these bytes were examined under this `hash_version` and yielded no artifact "
                "— and it is what keeps a re-run from decoding them again."
            ),
        ),
        sa.Column(
            "perceptual_hash_rotated",
            PRINTED,
            nullable=True,
            comment=(
                "The same hash of the artifact turned 180 degrees. `Normalized.quarter_turns` "
                "only puts the card's short edge first and makes no claim about which way up "
                "it is printed, so an artifact is in exactly one of two orientations and an "
                "upside-down retake hashes to something unrelated to its twin. **Stored "
                "rather than derived**: a 180-degree turn reverses the direction of the "
                "left-to-right comparison the hash is built from, so this is not a bit "
                "reversal of the column beside it."
            ),
        ),
        sa.Column(
            "hash_version",
            PRINTED,
            nullable=False,
            comment=(
                "What produced these — the hash, the detector and the normalizer, composed as "
                "`tcg_api.analysis.quality.PIPELINE_VERSION` composes its three. A row whose "
                "version is not the current one is recomputed by the next pass, which is the "
                "only invalidation there is. The **threshold is deliberately not in it**: "
                "nothing about the threshold is stored, so changing it invalidates no row."
            ),
        ),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(HASHES_ARE_HEX, name="hashes_are_lowercase_hex"),
        sa.CheckConstraint(
            "(perceptual_hash IS NULL) = (perceptual_hash_rotated IS NULL)",
            name="both_hashes_or_neither",
        ),
        sa.ForeignKeyConstraint(
            ["training_image_id"],
            ["training_images.id"],
            name="fk_training_image_fingerprints_image",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("training_image_id", name="pk_training_image_fingerprints"),
        comment=(
            "One perceptual fingerprint per training image — the **near-duplicate half** of "
            "deduplication, where uq_training_images_sha256 is the exact half. **Pairs and "
            "groups are deliberately not stored**: they are a pure function of these hashes "
            "and a threshold, computed when asked, exactly as `market_snapshots` derives its "
            "membership from a cut-line rather than listing it. A stored pair is a second "
            "answer that drifts from the first the moment the threshold moves. Not "
            "write-once: a version bump rewrites the row."
        ),
    )


def downgrade() -> None:
    # The table and nothing else. This revision creates no trigger function, and
    # `dataset_records_are_immutable()` still guards `dataset_versions` and
    # `dataset_members` — dropping it here would silently unguard both.
    op.drop_table("training_image_fingerprints")
