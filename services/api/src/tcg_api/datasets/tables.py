"""Spec §29's provenance, §31's versions and §32's grouping keys, as SQLAlchemy Core.

Four tables, and the constraint ADR 0008 exists to make unavoidable. Together
they say which physical object a photograph is of, what rights came with it,
and which frozen dataset version it was included in. **Core, not ORM**, on the
same terms as every other domain here.

The tables attach to the service-wide `MetaData` in `tcg_api.tables`, which
`database/migrations/env.py` compares a database against, and the domain is
registered in `tcg_api.table_registry` — a domain the registry does not import
is a domain `alembic revision --autogenerate` proposes dropping.

Six things about this schema are load-bearing:

* **The gate is a `CHECK`, and it is written with `IS TRUE`.** ADR 0009's whole
  argument for a database is that ADR 0008's rule — *a null, an empty string and
  an absent field are one answer, and it is refusal* — becomes a constraint
  rather than a function a loader must remember to call. The spelling matters as
  much as the placement: `NULL AND true` is `NULL`, and a `CHECK` **passes** on
  `NULL`, so a bare `commercial_use_allowed AND derivative_use_allowed` would
  admit exactly the unknown-provenance image the milestone exists to refuse. See
  :data:`_PROVENANCE_GATE`.
* **§29's nine fields are columns on the image row, not a table of their own.**
  ADR 0004 already ruled out a competing provenance table, §29 says *every image
  needs* the nine, and a constraint can only make an image unrepresentable while
  the rights sit on the same row as the bytes' digest.
* **The three fields the gate reads are nullable columns.** `NOT NULL` would
  refuse a null under a different constraint with a different message. One
  refusal, one name, one rule — which is what makes "unknown is false" reviewable
  rather than an emergent property of four constraints.
* **`redistribution_allowed` is recorded and never gated.** ADR 0008 makes it
  `false` on all four approved sources, including the photographs this project
  took itself, because the artwork is not ours. The column exists to record that,
  not to be waived, so it carries no `CHECK` on its value.
* **Immutability stops at the version.** §31 freezes a *dataset version*, so
  `dataset_versions` and `dataset_members` refuse an `UPDATE`. `physical_copies`
  and `training_images` deliberately do not: approved class 1 photographs a raw
  card and learns its certification number weeks later, a card is identified
  after ingestion, and ADR 0009 anticipates correcting provenance by script.
* **`source` carries no membership `CHECK`.** `grading_rules.company` and
  `economic_configurations.optimization_mode` set the precedent: a fifth approved
  source should cost an ADR and no migration. The allow-list is enforced where it
  changes — in the ingestion path — and the *rights* are enforced here, where they
  never change.

Spec §32's anti-leakage grouping is why `physical_copies` exists at all: two
photographs of one card must never land on opposite sides of a split, and none
of §29's nine fields identifies a physical object. Where a copy cannot be
identified — approved class 4, this product's own consented uploads —
`training_images.physical_copy_id` is NULL and the splitter falls back to
`source`, which §32 lists as an acceptable key precisely for that case. A
grouping key that is honestly coarse beats one that is confidently wrong.
"""

from __future__ import annotations

from typing import Final

import sqlalchemy as sa
from tcg_domain import VERSION_PATTERN, DatasetSplit, ImageSide
from tcg_grading_companies import GradingCompany

# `training_images.card_id` points into the catalog, so the catalog is a hard
# dependency of this module rather than merely of the migration environment: a
# `sa.ForeignKey` resolves against the `MetaData` it is attached to, and without
# `cards` on it `CreateTable(training_images)` raises NoReferencedTableError.
# Referenced as a column object rather than by the string "cards.id" for the
# reason `market/tables.py` gives: the dependency is then visible to a reader
# and to mypy, and cannot be a silent typo. The direction is safe — nothing in
# the catalog reads this domain.
from tcg_api.catalog.tables import cards
from tcg_api.tables import PRINTED, metadata, one_of

__all__ = [
    "PROVENANCE_FIELDS",
    "TABLES",
    "dataset_members",
    "dataset_versions",
    "physical_copies",
    "training_images",
]


#: Spec §29's nine fields, by the names this schema gives them. `source_url/
#: reference` is one field with two spellings in the specification and cannot be
#: a column name either way, so it is `source_reference`; the other eight are
#: §29's own words. Declared as a tuple so `test_datasets_tables.py` can assert
#: that all nine are present and that a tenth was not quietly added — ADR 0008
#: says in as many words that the per-copy identifier is *not* a tenth field.
PROVENANCE_FIELDS: Final = (
    "source",
    "source_reference",
    "acquisition_method",
    "license",
    "commercial_use_allowed",
    "derivative_use_allowed",
    "redistribution_allowed",
    "permission_notes",
    "acquired_at",
)

#: ADR 0008's gate. `IS TRUE` rather than a bare column reference, and that is
#: the single most important line in this module: a `CHECK` whose expression
#: evaluates to `NULL` **passes**, so `commercial_use_allowed AND
#: derivative_use_allowed` would admit a row that states neither. `IS TRUE`
#: returns false for `NULL`, which is ADR 0008's "unknown is refusal" written in
#: SQL. The same trap `cardinality` versus `array_length` documents on
#: `economic_configurations`.
#:
#: `btrim(license) <> ''` is the third of ADR 0008's three answers: an empty
#: string is not a licence, and both `btrim` and the comparison are IMMUTABLE,
#: which is what makes them legal in a constraint.
_PROVENANCE_GATE: Final = (
    "commercial_use_allowed IS TRUE "
    "AND derivative_use_allowed IS TRUE "
    "AND license IS NOT NULL "
    "AND btrim(license) <> ''"
)

#: The identifier grammar §31 requires — `pokemon-condition-v0.3.0`, never
#: `/latest/`. Taken from `tcg_domain`'s own pattern rather than retyped, so the
#: database refuses exactly what `CardDatabaseVersion` refuses. PostgreSQL's
#: advanced REs support `(?:…)`, so the expression transfers unchanged.
_VERSION_PATTERN: Final = VERSION_PATTERN.pattern

#: 64 lowercase hex characters, bare — the spelling `images.sha256` already
#: uses, because the column already names the algorithm.
_SHA256_PATTERN: Final = "^[0-9a-f]{64}$"


physical_copies = sa.Table(
    "physical_copies",
    metadata,
    sa.Column(
        "id",
        sa.Uuid(),
        primary_key=True,
        comment=(
            "**This is the per-copy identifier spec §32 groups on**, assigned when the "
            "card is acquired. No separate local reference column: a surrogate key "
            "already is one, and the ingestion CLI hands it to the operator. The card's "
            "catalog id is deliberately not it — two copies of one Charizard share a "
            "`card_id` and must be splittable apart, and one copy photographed twice "
            "shares no `sha256` and must not be."
        ),
    ),
    sa.Column(
        "certification_company",
        sa.Text(),
        nullable=True,
        comment=(
            "Which company slabbed this copy, where one has. NULL for a raw card and "
            "for every copy nobody has submitted yet — approved class 1 photographs a "
            "raw card and learns this weeks later, which is why nothing here is "
            "write-once."
        ),
    ),
    sa.Column(
        "certification_number",
        PRINTED,
        nullable=True,
        comment=(
            "The number printed on the slab. §32 names slab/certification among its "
            "grouping keys, so where one exists it *is* the identifier and this row "
            "records it rather than a parallel scheme being invented beside it."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # Two copies with one certification number are one copy entered twice, and
    # that is precisely the leakage §32 is about. NULLS NOT DISTINCT is
    # deliberately *not* set: every unidentified copy is its own row.
    #
    # Both names below are shortened away from what they describe, for the reason
    # `economic_configurations` gives: PostgreSQL truncates at 63 bytes and the
    # convention already spends 19 of them on `ck_physical_copies_`. The
    # convention's own rendering of this one is 61 and the supported-company
    # CHECK's is exactly 63 — a truncated name reflects back differently from the
    # declared one, and `--autogenerate` then reports a drop-and-re-add for ever.
    sa.UniqueConstraint(
        "certification_company",
        "certification_number",
        name="uq_physical_copies_certification",
    ),
    # The `market_observations` precedent: half a certification is not a smaller
    # certification, it is a row nobody can look up.
    sa.CheckConstraint(
        "(certification_company IS NULL) = (certification_number IS NULL)",
        name="certification_is_a_company_and_a_number",
    ),
    sa.CheckConstraint(
        f"certification_company IS NULL OR {one_of('certification_company', GradingCompany)}",
        name="certification_company_is_supported",
    ),
    # No `card_id`. Which card a copy is a copy of is recorded on the image,
    # which is the row that can exist before anybody has identified it; putting
    # it here as well would be one fact in two places that could disagree.
    # No index either: a copy is reached by primary key from its images, and the
    # unique constraint above serves the only lookup there is — "have I already
    # recorded this certification number?".
    comment=(
        "One physical card, however many photographs it has — spec §32's grouping key, "
        "and the gap ADR 0008 found in §29 while filling the nine fields in. "
        "Deliberately **not** write-once: a certification number arrives after the "
        "photographs do."
    ),
)


training_images = sa.Table(
    "training_images",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "physical_copy_id",
        sa.Uuid(),
        sa.ForeignKey(
            "physical_copies.id",
            ondelete="RESTRICT",
            name="fk_training_images_physical_copy_id_physical_copies",
        ),
        nullable=True,
        comment=(
            "Which physical object this is a photograph of — §32's grouping key. "
            "**NULL is an honest answer**, not a gap: approved class 4 is this "
            "product's own consented uploads, where the same user may analyse the same "
            "card twice and nothing identifies the copy. The splitter then groups by "
            "`source`, which §32 lists as an acceptable key precisely for this case. "
            "RESTRICT: a copy something was photographed against stays resolvable."
        ),
    ),
    sa.Column(
        "card_id",
        sa.Uuid(),
        sa.ForeignKey(cards.c.id, ondelete="RESTRICT", name="fk_training_images_card_id_cards"),
        nullable=True,
        comment=(
            "Which catalog card the photograph depicts. Nullable because a directory "
            "of photographs can be ingested before anyone has identified them, exactly "
            "as `analyses.card_id` is nullable until the user confirms. RESTRICT for "
            "the same reason it carries there: the catalog loaders only upsert, so "
            "nothing legitimate is blocked. **Never a grouping key** — two different "
            "copies of one card share this and §32 requires them to be splittable."
        ),
    ),
    sa.Column(
        "side",
        sa.Text(),
        nullable=False,
        comment=(
            "Which view of the card this is. The same six values `images.side` admits, "
            "read from the same `tcg_domain.analysis.ImageSide` — a training corpus and "
            "an uploaded analysis must not spell 'front' two ways."
        ),
    ),
    sa.Column(
        "original_uri",
        sa.Text(),
        nullable=False,
        comment=(
            "A server-generated storage key (ADR 0002, spec §55) — never a "
            "contributor-supplied filename or path. The bytes live in object storage "
            "and are never a column and never in git; this is the whole of the "
            "reference to them."
        ),
    ),
    sa.Column(
        "sha256",
        PRINTED,
        nullable=False,
        comment=(
            "A digest over the stored bytes, as 64 lowercase hex characters. **Unique "
            "here, where `images.sha256` is deliberately not**: the same photograph "
            "uploaded to two analyses is two images, and the same photograph ingested "
            "twice is one training image. That uniqueness is the exact-duplicate half "
            "of ADR 0009's deduplication; the near-duplicate half is a later issue's "
            "and needs no column here."
        ),
    ),
    sa.Column(
        "mime_type",
        sa.Text(),
        nullable=False,
        comment=(
            "The type the file was validated as, never the one a contributor claimed. "
            "No CHECK: which types are accepted is the ingestion path's policy, as on "
            "`images`, and will change without a migration."
        ),
    ),
    sa.Column(
        "width",
        sa.Integer(),
        nullable=False,
        comment=(
            "The stored image's width in pixels. NOT NULL, unlike `images.width`, "
            "which holds the *normalized* artifact's and stays empty until that stage "
            "has run: a training image is decoded to be validated, so the dimensions "
            "are known by the time the row exists."
        ),
    ),
    sa.Column(
        "height",
        sa.Integer(),
        nullable=False,
        comment="The stored image's height in pixels, as above.",
    ),
    # -- Spec §29's nine provenance fields, on the image row --------------------
    sa.Column(
        "source",
        PRINTED,
        nullable=False,
        comment=(
            "Where the image came from — 'first_party', 'contributed' or "
            "'product_upload' under ADR 0008. **No membership CHECK**, following "
            "`grading_rules.company`: a fifth approved source should cost an ADR and no "
            "migration, and the allow-list is enforced in the ingestion path where it "
            "changes. It is also §32's fallback grouping key wherever no physical copy "
            "could be identified, which is why it is COLLATE C."
        ),
    ),
    sa.Column(
        "source_reference",
        sa.Text(),
        nullable=True,
        comment=(
            "§29's `source_url/reference` — one field the specification spells two "
            "ways, and neither is a legal column name. Under ADR 0008 it is the "
            "submission's or slab's certification number for the two first-party "
            "classes, the signed grant's identifier for a contributed photograph, and "
            "the analysis identifier for a consented upload. **Deliberately not a "
            "foreign key into `analyses`**: spec §54 deletes that row on schedule and "
            "the training image outlives it."
        ),
    ),
    sa.Column(
        "acquisition_method",
        sa.Text(),
        nullable=False,
        comment=(
            "§29. How the image was obtained — 'photographed_before_submission', "
            "'photographed_owned_slab', 'contributed_under_written_grant' or "
            "'uploaded_by_user_with_consent'. Distinct from `source`, which names who "
            "it came from rather than how."
        ),
    ),
    sa.Column(
        "license",
        sa.Text(),
        nullable=True,
        comment=(
            "§29. What permits the use — ownership, the grant by identifier and date, "
            "or the consent text by version. Nullable **at the column** and refused by "
            "ck_training_images_provenance_permits_training, so that an absent licence "
            "and an unstated right are one refusal with one name rather than three "
            "constraints with three messages."
        ),
    ),
    sa.Column(
        "commercial_use_allowed",
        sa.Boolean(),
        nullable=True,
        comment=(
            "§29, and the field spec §29 names outright: the training pipeline rejects "
            "an image whose commercial-use status is unknown. **No server default, "
            "deliberately** — the `market_providers.commercial_use` precedent. A "
            "boolean that reads true because nobody wanted a null is the failure this "
            "milestone exists to prevent."
        ),
    ),
    sa.Column(
        "derivative_use_allowed",
        sa.Boolean(),
        nullable=True,
        comment=(
            "§29. Gated beside commercial use rather than merely recorded, because "
            "spec §28's pipeline ends in Training and a trained model is a derivative "
            "work. No server default, as above."
        ),
    ),
    sa.Column(
        "redistribution_allowed",
        sa.Boolean(),
        nullable=False,
        comment=(
            "§29. **Recorded and never gated**: ADR 0008 makes it false on all four "
            "approved sources, including the photographs this project took itself, "
            "because the artwork in them is not ours. NOT NULL because nothing else "
            "guards it, and no CHECK on its value — the column exists to record the "
            "answer, not to be waived. It is why no dataset is ever published and why "
            "a manifest of identifiers and hashes is all a version leaves behind."
        ),
    ),
    sa.Column(
        "permission_notes",
        sa.Text(),
        nullable=True,
        comment=(
            "§29. Free text: the grant's own limits, the consent version, and ADR "
            "0008's standing risk R1 — the artwork layer, which is not ours and is not "
            "grantable by anyone who has granted us anything."
        ),
    ),
    sa.Column(
        "acquired_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        comment=(
            "§29. When the photograph was taken or the upload was made — the fact "
            "about the image, not about this row, which is `created_at`."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # ADR 0008's gate, and the reason this store is a database at all.
    sa.CheckConstraint(_PROVENANCE_GATE, name="provenance_permits_training"),
    sa.UniqueConstraint("sha256", name="uq_training_images_sha256"),
    sa.CheckConstraint(f"sha256 ~ '{_SHA256_PATTERN}'", name="sha256_is_lowercase_hex"),
    sa.CheckConstraint(one_of("side", ImageSide), name="side_is_a_known_side"),
    sa.CheckConstraint("width > 0 AND height > 0", name="dimensions_are_positive"),
    # Neither of these is empty-string tolerant: a source nobody named and a
    # method nobody recorded are the same unknown the gate refuses one column
    # over, and refusing them here keeps `source` usable as §32's fallback key.
    sa.CheckConstraint("btrim(source) <> ''", name="source_is_not_blank"),
    sa.CheckConstraint("btrim(acquisition_method) <> ''", name="acquisition_method_is_not_blank"),
    # §32's grouping query — every image of one physical copy. Partial, because
    # the column is NULL for every consented upload and those rows are grouped by
    # `source` instead.
    sa.Index(
        "ix_training_images_physical_copy_id",
        "physical_copy_id",
        postgresql_where=sa.text("physical_copy_id IS NOT NULL"),
    ),
    # The other grouping key, and the only way to count a version's provenance
    # mix. No index on `card_id`: nothing deletes from the catalog, so the
    # RESTRICT check never runs in anger, and no query yet asks which training
    # images depict a card.
    sa.Index("ix_training_images_source", "source"),
    comment=(
        "One training image and the rights that came with it — spec §29's nine fields "
        "on the same row as the digest, which is what lets "
        "ck_training_images_provenance_permits_training make an image nobody may train "
        "on unrepresentable. Not write-once: a card is identified after ingestion and "
        "ADR 0009 anticipates correcting provenance by script."
    ),
)


dataset_versions = sa.Table(
    "dataset_versions",
    metadata,
    sa.Column(
        "id",
        sa.Uuid(),
        primary_key=True,
        comment="A surrogate key. The record's identity is `version`.",
    ),
    sa.Column(
        "ordinal",
        sa.BigInteger(),
        sa.Identity(always=True, start=1),
        nullable=False,
        comment=(
            "Publication order, assigned by the database. GENERATED ALWAYS so no writer "
            "can place a version out of sequence. The `card_database_versions` shape "
            "(#27), reused rather than reinvented."
        ),
    ),
    sa.Column(
        "version",
        PRINTED,
        nullable=False,
        comment=(
            "An explicit, ordered identifier — 'pokemon-condition-v0.3.0'. Spec §31 "
            "requires every training run to reference one of these and forbids a model "
            "referencing '/latest/'; the CHECK on its grammar is what makes '/latest/' "
            "unstorable rather than merely discouraged."
        ),
    ),
    sa.Column(
        "split_seed",
        sa.BigInteger(),
        nullable=False,
        comment=(
            "The seed spec §32's splitter ran with. Stored because it is derivable from "
            "nothing and a split that cannot be reproduced makes a version reproducible "
            "in name only. The proportions actually achieved are **not** stored: those "
            "are a count over `dataset_members`, and a stored copy is a second answer "
            "that can drift from the first."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.UniqueConstraint("version", name="uq_dataset_versions_version"),
    # An identity column hands out distinct values; it does not promise them —
    # a sequence can be restarted. This constraint is the promise, and it is
    # also the index `ORDER BY ordinal DESC LIMIT 1` uses.
    sa.UniqueConstraint("ordinal", name="uq_dataset_versions_ordinal"),
    sa.CheckConstraint(f"version ~ '{_VERSION_PATTERN}'", name="version_is_an_explicit_identifier"),
    comment=(
        "One frozen corpus — spec §31's `dataset_version`. Write-once: "
        "trg_dataset_versions_immutable refuses an UPDATE, so a re-split is a new "
        "version rather than an edit, which is the only thing that makes a past "
        "training run re-derivable."
    ),
)


dataset_members = sa.Table(
    "dataset_members",
    metadata,
    sa.Column(
        "dataset_version_id",
        sa.Uuid(),
        sa.ForeignKey(
            "dataset_versions.id",
            ondelete="CASCADE",
            name="fk_dataset_members_dataset_version_id_dataset_versions",
        ),
        nullable=False,
        comment=(
            "CASCADE: a membership row means nothing without its version, exactly as "
            "`card_external_ids` means nothing without its card."
        ),
    ),
    sa.Column(
        "training_image_id",
        sa.Uuid(),
        sa.ForeignKey(
            "training_images.id",
            ondelete="RESTRICT",
            name="fk_dataset_members_training_image_id_training_images",
        ),
        nullable=False,
        comment=(
            "**RESTRICT is the point.** ADR 0008 grants retention after a contributor "
            "withdraws precisely because §31 means a version cannot un-include an "
            "image; deleting the image out from under a frozen version would leave a "
            "manifest naming bytes nobody can produce."
        ),
    ),
    sa.Column(
        "split",
        sa.Text(),
        nullable=False,
        comment=(
            "train, validation or test — spec §32. **This table is a real membership "
            "list, and that differs from `market_snapshots` (#51) on purpose**: a "
            "snapshot stores no members because its membership is derivable from a "
            "cut-line on `created_at`, where a train/validation/test assignment is a "
            "decision and is derivable from nothing. The next reader will otherwise "
            "assume the two should match."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # The natural key is the whole row's identity: one image appears in a version
    # once, in one split. No surrogate id — there is nothing to reference it by.
    sa.PrimaryKeyConstraint("dataset_version_id", "training_image_id", name="pk_dataset_members"),
    sa.CheckConstraint(one_of("split", DatasetSplit), name="split_is_a_known_split"),
    # "Which versions is this image in?" — the RESTRICT check's query, and the
    # only one the primary key does not already serve, since it leads with the
    # version.
    sa.Index("ix_dataset_members_training_image_id", "training_image_id"),
    comment=(
        "One image's place in one frozen dataset version — spec §32's assignment. "
        "Write-once: trg_dataset_members_immutable refuses an UPDATE. That a member "
        "cannot be *added* afterwards is the versioning issue's to hold, by writing "
        "the members inside the transaction that creates the version."
    ),
)


#: Every table this module contributes to the shared `MetaData`, in creation
#: order — `dataset_members` references two of the others.
TABLES: Final = (physical_copies, training_images, dataset_versions, dataset_members)


# ---------------------------------------------------------------------------
# A dataset version is frozen, as a database guarantee rather than a promise
# ---------------------------------------------------------------------------
# One function for both tables, as `market_rows_are_immutable()` serves three:
# `TG_TABLE_NAME` is what tells the refusals apart, and one function is one
# thing to drop.
#
# **UPDATE only, deliberately not DELETE**, following the market tables and
# `economic_configurations`. Spec §54's disposal rules and a withdrawn
# contributor both need rows to be removable; what must not happen is a frozen
# version quietly meaning something different from what a past training run read.
# A version something references is undeletable anyway, through the foreign
# key's RESTRICT.
#
# `physical_copies` and `training_images` carry no trigger at all, and that is a
# decision rather than an omission — see the module docstring.
#
# Three standing caveats, as in every other domain: `plpgsql` needs no
# `CREATE EXTENSION`; TRUNCATE bypasses row-level triggers, which is the only
# reason the integration fixtures can empty these tables; and Alembic compares
# no triggers at all, so `test_datasets_schema.py`'s refusal tests are the only
# guard against this and the migration drifting apart.
def _ddl(statement: str) -> sa.DDL:
    """`sa.DDL` is unannotated in SQLAlchemy's own types, and mypy runs strict here."""
    return sa.DDL(statement)  # type: ignore[no-untyped-call]


# `RAISE USING MESSAGE = ...`, concatenated, rather than `RAISE EXCEPTION 'x %',
# arg`: `sa.DDL` runs its statement through Python's `%` interpolation, so a
# format specifier in the body fails at compile time. Do not "simplify" it back.
_IMMUTABLE_FUNCTION: Final = _ddl(
    """
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
)

_VERSIONS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_dataset_versions_immutable
    BEFORE UPDATE ON dataset_versions
    FOR EACH ROW EXECUTE FUNCTION dataset_records_are_immutable();
    """
)

_MEMBERS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_dataset_members_immutable
    BEFORE UPDATE ON dataset_members
    FOR EACH ROW EXECUTE FUNCTION dataset_records_are_immutable();
    """
)

# Two statements, two DDL objects: the asyncpg driver prepares each statement it
# is handed, and a prepared statement may not contain more than one.
_DROP_VERSIONS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_dataset_versions_immutable ON dataset_versions"
)

_DROP_MEMBERS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_dataset_members_immutable ON dataset_members"
)

_DROP_IMMUTABLE_FUNCTION: Final = _ddl("DROP FUNCTION IF EXISTS dataset_records_are_immutable()")

sa.event.listen(
    dataset_versions, "after_create", _IMMUTABLE_FUNCTION.execute_if(dialect="postgresql")
)
sa.event.listen(
    dataset_versions, "after_create", _VERSIONS_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(dataset_members, "after_create", _MEMBERS_TRIGGER.execute_if(dialect="postgresql"))
sa.event.listen(
    dataset_members, "before_drop", _DROP_MEMBERS_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(
    dataset_versions, "before_drop", _DROP_VERSIONS_TRIGGER.execute_if(dialect="postgresql")
)
# Dropped last, and attached to `dataset_versions` because `DROP TABLE` takes
# each trigger with it but never the function the two share.
sa.event.listen(
    dataset_versions,
    "before_drop",
    _DROP_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
