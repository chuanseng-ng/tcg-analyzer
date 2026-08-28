"""add the dataset and provenance schema

Spec §69/M6 needs a store before anything can be ingested, and ADR 0008 forbids
collecting a training image before the gate that admits it exists. This revision
creates the sixth schema domain: spec §32's `physical_copies`, spec §29's
`training_images` with the nine provenance fields on the same row as the digest,
and spec §31's `dataset_versions` with their `dataset_members`.

The shape and the reasoning live in
`services/api/src/tcg_api/datasets/tables.py`, which is what `env.py` compares a
database against; the DDL is written out again here rather than imported from
it, because a migration is a snapshot of what was applied and must not change
when that module does.

Five things worth knowing before reading the DDL:

* **`ck_training_images_provenance_permits_training` is what this revision is
  for.** ADR 0009 chose a database over files precisely so ADR 0008's rule — *a
  null, an empty string and an absent field are one answer, and it is refusal* —
  is a constraint rather than a function a loader must remember to call. It is
  written with `IS TRUE` and not with a bare column reference: a `CHECK` whose
  expression evaluates to `NULL` **passes**, so the obvious spelling would admit
  exactly the unknown-provenance image the milestone exists to refuse.
* **The three fields the gate reads are nullable columns**, so an absent licence
  and an unstated right are one refusal with one name. `NOT NULL` would refuse a
  null under a different constraint with a different message.
* **`redistribution_allowed` is NOT NULL and carries no CHECK on its value.** ADR
  0008 makes it false on all four approved sources; the column records that
  answer and is not a switch to be waived.
* **`source` carries no membership CHECK**, following `grading_rules.company` and
  `economic_configurations.optimization_mode`: a fifth approved source should
  cost an ADR and no migration. The rights are enforced here, where they never
  change; the allow-list is enforced in the ingestion path, where it does.
* **`dataset_members` is a real membership list, and that differs from
  `market_snapshots` on purpose.** A snapshot stores no members because its
  membership is derivable from a cut-line on `created_at`; a train/validation/
  test assignment is a decision and is derivable from nothing.

The write-once trigger covers `dataset_versions` and `dataset_members` and
deliberately not the other two: §31 freezes a *version*, while a certification
number arrives after the photographs do and a card is identified after
ingestion. One function serves both tables, as `market_rows_are_immutable()`
serves three — `TG_TABLE_NAME` is what tells the refusals apart.

**UPDATE only, deliberately not DELETE**, as every other domain here. A version
something references is undeletable anyway, through the foreign key's RESTRICT.

No rows are inserted. Nothing has been ingested, and ADR 0008 admits an image
only through the ingestion path a later issue builds.

Alembic compares no triggers, so `compare_metadata` will not notice if this and
`tables.py` drift apart. `services/api/tests/test_datasets_schema.py` asserts an
UPDATE is actually refused; that test is the only guard there is.

Revision ID: 6f49252e81d4
Revises: 9d2f61c47ab3
Create Date: 2026-08-28 00:00:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6f49252e81d4"
down_revision: str | None = "9d2f61c47ab3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Printed text, ordered by byte rather than by whatever locale the server was
# initialised with — the same reason the catalog revision gives at length.
PRINTED = sa.Text(collation="C")

# Written out as literals rather than built from the domain the table module
# reads them from. A migration is a snapshot of what was applied;
# `test_datasets_tables.py` checks that the two still agree.
VERSION_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*-v\d+\.\d+\.\d+$"
SHA256_PATTERN = "^[0-9a-f]{64}$"

# ADR 0008's gate, and the single most important line in this revision.
# `IS TRUE` rather than a bare column reference, because `NULL AND true` is
# `NULL` and a `CHECK` **passes** on `NULL` — the same trap `cardinality` versus
# `array_length` documents on `economic_configurations`. `btrim(license) <> ''`
# is the third of ADR 0008's three answers: an empty string is not a licence.
PROVENANCE_GATE = (
    "commercial_use_allowed IS TRUE "
    "AND derivative_use_allowed IS TRUE "
    "AND license IS NOT NULL "
    "AND btrim(license) <> ''"
)

# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: the same statement is declared through `sa.DDL` in `tables.py`, and
# `sa.DDL` runs its statement through Python's `%` interpolation, so a format
# specifier in the body fails at compile time. Kept identical here so the two
# copies can be diffed by eye.
#
# `restrict_violation` is SQLSTATE class 23, which SQLAlchemy surfaces as an
# `IntegrityError` — the same shape as every other constraint in this schema,
# rather than an `InternalError` a caller would have to special-case.
IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION dataset_records_are_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE USING
        ERRCODE = 'restrict_violation',
        MESSAGE = TG_TABLE_NAME || ' is frozen: '
                  || TG_OP || ' was refused',
        HINT    = 'Publish a new dataset version rather than rewriting one.';
END;
$$;
"""

VERSIONS_TRIGGER = """
CREATE TRIGGER trg_dataset_versions_immutable
BEFORE UPDATE ON dataset_versions
FOR EACH ROW EXECUTE FUNCTION dataset_records_are_immutable();
"""

MEMBERS_TRIGGER = """
CREATE TRIGGER trg_dataset_members_immutable
BEFORE UPDATE ON dataset_members
FOR EACH ROW EXECUTE FUNCTION dataset_records_are_immutable();
"""


def upgrade() -> None:
    op.create_table(
        "physical_copies",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="**This is the per-copy identifier spec §32 groups on**, assigned when the card is acquired. No separate local reference column: a surrogate key already is one, and the ingestion CLI hands it to the operator. The card's catalog id is deliberately not it — two copies of one Charizard share a `card_id` and must be splittable apart, and one copy photographed twice shares no `sha256` and must not be.",
        ),
        sa.Column(
            "certification_company",
            sa.Text(),
            nullable=True,
            comment="Which company slabbed this copy, where one has. NULL for a raw card and for every copy nobody has submitted yet — approved class 1 photographs a raw card and learns this weeks later, which is why nothing here is write-once.",
        ),
        sa.Column(
            "certification_number",
            PRINTED,
            nullable=True,
            comment="The number printed on the slab. §32 names slab/certification among its grouping keys, so where one exists it *is* the identifier and this row records it rather than a parallel scheme being invented beside it.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "certification_company IS NULL OR certification_company IN ('psa', 'tag', 'bgs')",
            name="certification_company_is_supported",
        ),
        sa.CheckConstraint(
            "(certification_company IS NULL) = (certification_number IS NULL)",
            name="certification_is_a_company_and_a_number",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_physical_copies"),
        sa.UniqueConstraint(
            "certification_company", "certification_number", name="uq_physical_copies_certification"
        ),
        comment="One physical card, however many photographs it has — spec §32's grouping key, and the gap ADR 0008 found in §29 while filling the nine fields in. Deliberately **not** write-once: a certification number arrives after the photographs do.",
    )
    op.create_table(
        "training_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "physical_copy_id",
            sa.Uuid(),
            nullable=True,
            comment="Which physical object this is a photograph of — §32's grouping key. **NULL is an honest answer**, not a gap: approved class 4 is this product's own consented uploads, where the same user may analyse the same card twice and nothing identifies the copy. The splitter then groups by `source`, which §32 lists as an acceptable key precisely for this case. RESTRICT: a copy something was photographed against stays resolvable.",
        ),
        sa.Column(
            "card_id",
            sa.Uuid(),
            nullable=True,
            comment="Which catalog card the photograph depicts. Nullable because a directory of photographs can be ingested before anyone has identified them, exactly as `analyses.card_id` is nullable until the user confirms. RESTRICT for the same reason it carries there: the catalog loaders only upsert, so nothing legitimate is blocked. **Never a grouping key** — two different copies of one card share this and §32 requires them to be splittable.",
        ),
        sa.Column(
            "side",
            sa.Text(),
            nullable=False,
            comment="Which view of the card this is. The same six values `images.side` admits, read from the same `tcg_domain.analysis.ImageSide` — a training corpus and an uploaded analysis must not spell 'front' two ways.",
        ),
        sa.Column(
            "original_uri",
            sa.Text(),
            nullable=False,
            comment="A server-generated storage key (ADR 0002, spec §55) — never a contributor-supplied filename or path. The bytes live in object storage and are never a column and never in git; this is the whole of the reference to them.",
        ),
        sa.Column(
            "sha256",
            PRINTED,
            nullable=False,
            comment="A digest over the stored bytes, as 64 lowercase hex characters. **Unique here, where `images.sha256` is deliberately not**: the same photograph uploaded to two analyses is two images, and the same photograph ingested twice is one training image. That uniqueness is the exact-duplicate half of ADR 0009's deduplication; the near-duplicate half is a later issue's and needs no column here.",
        ),
        sa.Column(
            "mime_type",
            sa.Text(),
            nullable=False,
            comment="The type the file was validated as, never the one a contributor claimed. No CHECK: which types are accepted is the ingestion path's policy, as on `images`, and will change without a migration.",
        ),
        sa.Column(
            "width",
            sa.Integer(),
            nullable=False,
            comment="The stored image's width in pixels. NOT NULL, unlike `images.width`, which holds the *normalized* artifact's and stays empty until that stage has run: a training image is decoded to be validated, so the dimensions are known by the time the row exists.",
        ),
        sa.Column(
            "height",
            sa.Integer(),
            nullable=False,
            comment="The stored image's height in pixels, as above.",
        ),
        sa.Column(
            "source",
            PRINTED,
            nullable=False,
            comment="Where the image came from — 'first_party', 'contributed' or 'product_upload' under ADR 0008. **No membership CHECK**, following `grading_rules.company`: a fifth approved source should cost an ADR and no migration, and the allow-list is enforced in the ingestion path where it changes. It is also §32's fallback grouping key wherever no physical copy could be identified, which is why it is COLLATE C.",
        ),
        sa.Column(
            "source_reference",
            sa.Text(),
            nullable=True,
            comment="§29's `source_url/reference` — one field the specification spells two ways, and neither is a legal column name. Under ADR 0008 it is the submission's or slab's certification number for the two first-party classes, the signed grant's identifier for a contributed photograph, and the analysis identifier for a consented upload. **Deliberately not a foreign key into `analyses`**: spec §54 deletes that row on schedule and the training image outlives it.",
        ),
        sa.Column(
            "acquisition_method",
            sa.Text(),
            nullable=False,
            comment="§29. How the image was obtained — 'photographed_before_submission', 'photographed_owned_slab', 'contributed_under_written_grant' or 'uploaded_by_user_with_consent'. Distinct from `source`, which names who it came from rather than how.",
        ),
        sa.Column(
            "license",
            sa.Text(),
            nullable=True,
            comment="§29. What permits the use — ownership, the grant by identifier and date, or the consent text by version. Nullable **at the column** and refused by ck_training_images_provenance_permits_training, so that an absent licence and an unstated right are one refusal with one name rather than three constraints with three messages.",
        ),
        sa.Column(
            "commercial_use_allowed",
            sa.Boolean(),
            nullable=True,
            comment="§29, and the field spec §29 names outright: the training pipeline rejects an image whose commercial-use status is unknown. **No server default, deliberately** — the `market_providers.commercial_use` precedent. A boolean that reads true because nobody wanted a null is the failure this milestone exists to prevent.",
        ),
        sa.Column(
            "derivative_use_allowed",
            sa.Boolean(),
            nullable=True,
            comment="§29. Gated beside commercial use rather than merely recorded, because spec §28's pipeline ends in Training and a trained model is a derivative work. No server default, as above.",
        ),
        sa.Column(
            "redistribution_allowed",
            sa.Boolean(),
            nullable=False,
            comment="§29. **Recorded and never gated**: ADR 0008 makes it false on all four approved sources, including the photographs this project took itself, because the artwork in them is not ours. NOT NULL because nothing else guards it, and no CHECK on its value — the column exists to record the answer, not to be waived. It is why no dataset is ever published and why a manifest of identifiers and hashes is all a version leaves behind.",
        ),
        sa.Column(
            "permission_notes",
            sa.Text(),
            nullable=True,
            comment="§29. Free text: the grant's own limits, the consent version, and ADR 0008's standing risk R1 — the artwork layer, which is not ours and is not grantable by anyone who has granted us anything.",
        ),
        sa.Column(
            "acquired_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="§29. When the photograph was taken or the upload was made — the fact about the image, not about this row, which is `created_at`.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(acquisition_method) <> ''", name="acquisition_method_is_not_blank"
        ),
        sa.CheckConstraint("btrim(source) <> ''", name="source_is_not_blank"),
        sa.CheckConstraint(PROVENANCE_GATE, name="provenance_permits_training"),
        sa.CheckConstraint(f"sha256 ~ '{SHA256_PATTERN}'", name="sha256_is_lowercase_hex"),
        sa.CheckConstraint(
            "side IN ('front', 'back', 'angled_front', 'angled_back', 'surface_front', 'surface_back')",
            name="side_is_a_known_side",
        ),
        sa.CheckConstraint("width > 0 AND height > 0", name="dimensions_are_positive"),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_training_images_card_id_cards", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["physical_copy_id"],
            ["physical_copies.id"],
            name="fk_training_images_physical_copy_id_physical_copies",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_training_images"),
        sa.UniqueConstraint("sha256", name="uq_training_images_sha256"),
        comment="One training image and the rights that came with it — spec §29's nine fields on the same row as the digest, which is what lets ck_training_images_provenance_permits_training make an image nobody may train on unrepresentable. Not write-once: a card is identified after ingestion and ADR 0009 anticipates correcting provenance by script.",
    )
    op.create_index(
        "ix_training_images_physical_copy_id",
        "training_images",
        ["physical_copy_id"],
        unique=False,
        postgresql_where=sa.text("physical_copy_id IS NOT NULL"),
    )
    op.create_index("ix_training_images_source", "training_images", ["source"], unique=False)
    op.create_table(
        "dataset_versions",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            comment="A surrogate key. The record's identity is `version`.",
        ),
        sa.Column(
            "ordinal",
            sa.BigInteger(),
            sa.Identity(always=True, start=1),
            nullable=False,
            comment="Publication order, assigned by the database. GENERATED ALWAYS so no writer can place a version out of sequence. The `card_database_versions` shape (#27), reused rather than reinvented.",
        ),
        sa.Column(
            "version",
            PRINTED,
            nullable=False,
            comment="An explicit, ordered identifier — 'pokemon-condition-v0.3.0'. Spec §31 requires every training run to reference one of these and forbids a model referencing '/latest/'; the CHECK on its grammar is what makes '/latest/' unstorable rather than merely discouraged.",
        ),
        sa.Column(
            "split_seed",
            sa.BigInteger(),
            nullable=False,
            comment="The seed spec §32's splitter ran with. Stored because it is derivable from nothing and a split that cannot be reproduced makes a version reproducible in name only. The proportions actually achieved are **not** stored: those are a count over `dataset_members`, and a stored copy is a second answer that can drift from the first.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"version ~ '{VERSION_PATTERN}'", name="version_is_an_explicit_identifier"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_versions"),
        sa.UniqueConstraint("ordinal", name="uq_dataset_versions_ordinal"),
        sa.UniqueConstraint("version", name="uq_dataset_versions_version"),
        comment="One frozen corpus — spec §31's `dataset_version`. Write-once: trg_dataset_versions_immutable refuses an UPDATE, so a re-split is a new version rather than an edit, which is the only thing that makes a past training run re-derivable.",
    )
    op.create_table(
        "dataset_members",
        sa.Column(
            "dataset_version_id",
            sa.Uuid(),
            nullable=False,
            comment="CASCADE: a membership row means nothing without its version, exactly as `card_external_ids` means nothing without its card.",
        ),
        sa.Column(
            "training_image_id",
            sa.Uuid(),
            nullable=False,
            comment="**RESTRICT is the point.** ADR 0008 grants retention after a contributor withdraws precisely because §31 means a version cannot un-include an image; deleting the image out from under a frozen version would leave a manifest naming bytes nobody can produce.",
        ),
        sa.Column(
            "split",
            sa.Text(),
            nullable=False,
            comment="train, validation or test — spec §32. **This table is a real membership list, and that differs from `market_snapshots` (#51) on purpose**: a snapshot stores no members because its membership is derivable from a cut-line on `created_at`, where a train/validation/test assignment is a decision and is derivable from nothing. The next reader will otherwise assume the two should match.",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "split IN ('train', 'validation', 'test')", name="split_is_a_known_split"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name="fk_dataset_members_dataset_version_id_dataset_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["training_image_id"],
            ["training_images.id"],
            name="fk_dataset_members_training_image_id_training_images",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_version_id", "training_image_id", name="pk_dataset_members"
        ),
        comment="One image's place in one frozen dataset version — spec §32's assignment. Write-once: trg_dataset_members_immutable refuses an UPDATE. That a member cannot be *added* afterwards is the versioning issue's to hold, by writing the members inside the transaction that creates the version.",
    )
    op.create_index(
        "ix_dataset_members_training_image_id",
        "dataset_members",
        ["training_image_id"],
        unique=False,
    )

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(VERSIONS_TRIGGER)
    op.execute(MEMBERS_TRIGGER)


def downgrade() -> None:
    # `DROP TABLE` would take each trigger with it, but not the function they
    # share, so the function is named explicitly and dropped last.
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_members_immutable ON dataset_members")
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_versions_immutable ON dataset_versions")
    op.drop_index("ix_dataset_members_training_image_id", table_name="dataset_members")
    op.drop_table("dataset_members")
    op.drop_table("dataset_versions")
    op.drop_index("ix_training_images_source", table_name="training_images")
    op.drop_index(
        "ix_training_images_physical_copy_id",
        table_name="training_images",
        postgresql_where=sa.text("physical_copy_id IS NOT NULL"),
    )
    op.drop_table("training_images")
    op.drop_table("physical_copies")
    op.execute("DROP FUNCTION IF EXISTS dataset_records_are_immutable()")
