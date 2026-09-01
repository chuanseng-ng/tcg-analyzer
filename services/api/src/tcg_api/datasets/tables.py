"""Spec §29's provenance, §30's annotations, §31's versions and §32's grouping keys.

Eight tables, as SQLAlchemy Core, and the constraint ADR 0008 exists to make
unavoidable. Together they say which physical object a photograph is of, what
rights came with it, which other photographs it is a near duplicate of, what is
wrong with the card in it, what grade the card itself came back with, and which
frozen dataset version it was included in.
**Core, not ORM**, on the same terms as every other domain here.

The tables attach to the service-wide `MetaData` in `tcg_api.tables`, which
`database/migrations/env.py` compares a database against, and the domain is
registered in `tcg_api.table_registry` — a domain the registry does not import
is a domain `alembic revision --autogenerate` proposes dropping.

Nine things about this schema are load-bearing:

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
* **Immutability stops at the version, and starts again at the annotation.**
  §31 freezes a *dataset version*, so `dataset_versions` and `dataset_members`
  refuse an `UPDATE`, and so do `image_annotations` and `centering_measurements`
  — a corrected annotation is a new row, because a version that referenced the
  old reading must keep meaning what it meant. `physical_copies` and
  `training_images` deliberately do not: approved class 1 photographs a raw card
  and learns its certification number weeks later, a card is identified after
  ingestion, and ADR 0009 anticipates correcting provenance by script. Two
  trigger functions, not one, and only because the two hints differ: see
  :data:`_ANNOTATION_IMMUTABLE_FUNCTION`.
* **`source` carries no membership `CHECK`.** `grading_rules.company` and
  `economic_configurations.optimization_mode` set the precedent: a fifth approved
  source should cost an ADR and no migration. The allow-list is enforced where it
  changes — in the ingestion path — and the *rights* are enforced here, where they
  never change.
* **A marker and a measurement are two tables.** §30 asks for corner, edge and
  surface defect annotation *and* centering measurements, and they share no field
  but the annotator and the time: a marker carries a label, a severity and a
  bounding box, a measurement carries two ratios and none of those. One table
  with a `kind` would leave half of every row NULL by construction.
* **A grade is stored under the *grammar* of a grade, and which grades a
  company issues is checked in Python.** `market_observations` set that split
  and the reason transfers unchanged: PSA and TAG issue no 9.5 and BGS does, so
  a per-company `CHECK` would make a fourth company — or a scale revision —
  cost a migration of `grading_outcomes`. See :data:`_ISSUED_GRADE_PATTERN`, and
  `tcg_api.datasets.outcomes` for the guard that reads
  `GradeScale.supports`.
* **An annotation's coordinates are fractions of the representation the row
  names.** #38 warps every image to one 756x1056 artifact, so a coordinate in
  that space survives a retake and compares across cards; a raw-photograph
  coordinate would be meaningless the moment the framing changed. That was the
  whole rule until ADR 0010 measured that the artifact cannot resolve §16's fine
  defect classes, and #175 admitted the one argued exception: a *surface*
  annotation may declare its coordinates fractions of the original photograph,
  and `representation` is that declaration on the row rather than a convention.
  Fractions rather than pixels of either frame, which is also what keeps this
  module free of `ml/normalization` — importing it for two integers would put
  OpenCV in the API image, and `test_import_purity.py` forbids that.

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
from sqlalchemy.dialects import postgresql
from tcg_domain import VERSION_PATTERN, DatasetSplit, ImageSide
from tcg_domain.annotation import (
    LABELS_BY_KIND,
    NO_DEFECT_LABELS,
    REGIONS_BY_KIND,
    AnnotationKind,
    DefectSeverity,
)
from tcg_grading_companies import Designation, GradingCompany

# `training_images.card_id` points into the catalog, so the catalog is a hard
# dependency of this module rather than merely of the migration environment: a
# `sa.ForeignKey` resolves against the `MetaData` it is attached to, and without
# `cards` on it `CreateTable(training_images)` raises NoReferencedTableError.
# Referenced as a column object rather than by the string "cards.id" for the
# reason `market/tables.py` gives: the dependency is then visible to a reader
# and to mypy, and cannot be a silent typo. The direction is safe — nothing in
# the catalog reads this domain.
from tcg_api.catalog.tables import cards
from tcg_api.tables import NO_METADATA, PRINTED, metadata, one_of

__all__ = [
    "PROVENANCE_FIELDS",
    "SUBGRADE_COLUMNS",
    "TABLES",
    "centering_measurements",
    "dataset_members",
    "dataset_versions",
    "grading_outcomes",
    "image_annotations",
    "physical_copies",
    "training_image_fingerprints",
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

#: One point on a grade scale, as `tcg_domain.Grade` renders it. `10` is spelled
#: out because `10.5` is not a grade and `[0-9](\.5)?` cannot say so —
#: `market_observations._GRADE_KEY_PATTERN`, minus §24's collapsed tails.
#:
#: **Dropping `_or_lower` / `_or_higher` is the difference and it is deliberate.**
#: A collapsed tail is something a model emits when it will not commit to one
#: point. A slab prints one point, so a bucket here would be a distribution
#: wearing an outcome's clothes.
_ISSUED_GRADE_PATTERN: Final = r"^(10|[0-9](\.5)?)$"


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
    # -- The standardized artifact an annotator judges, and #160 measures against -
    sa.Column(
        "normalized_uri",
        sa.Text(),
        nullable=True,
        comment=(
            "A server-generated storage key for the perspective-corrected, cropped "
            "artifact spec §30's annotation tool shows and §21's coordinates are "
            "fractions of. NULL until `tcg-normalize-training-images` has run, and "
            "still NULL afterwards where no card was located — there was nothing to "
            "straighten. `images.normalized_uri`'s meaning, on the corpus."
        ),
    ),
    sa.Column(
        "normalization_details",
        postgresql.JSONB(),
        nullable=True,
        comment=(
            "How `normalized_uri` was produced: the projective transform from the "
            "photograph, the quarter-turn applied, the artifact's size, the stage "
            "version and the thresholds it ran with — `Normalized.as_record()`, the "
            "shape `images.normalization_details` already carries. The artifact's size "
            "lives here rather than in two columns beside `width` and `height`, "
            "because those two are the *photograph's* and must keep meaning that. "
            "It records what produced an artifact; it is deliberately **not** a "
            "staleness check, because §30's coordinates are fractions of the "
            "artifact an annotator actually saw and re-warping one under a bumped "
            "version would move every stored fraction without touching a row."
        ),
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


#: The grammar of a 64-bit difference hash rendered as text: 16 lowercase hex
#: characters, the spelling `_SHA256_PATTERN` uses one table up. Text rather than
#: a `BIGINT` for that consistency, and because a 64-bit hash does not fit a
#: signed bigint without a cast nobody should have to remember.
_PERCEPTUAL_HASH_PATTERN: Final = "^[0-9a-f]{16}$"

#: Both hash columns share one grammar, so they share one CHECK. Composed rather
#: than written out, so the pattern above is the only place the width is stated —
#: `test_datasets_tables.py` holds this against the migration's copy by value.
_HASHES_ARE_HEX: Final = (
    f"(perceptual_hash IS NULL OR perceptual_hash ~ '{_PERCEPTUAL_HASH_PATTERN}') "
    f"AND (perceptual_hash_rotated IS NULL "
    f"OR perceptual_hash_rotated ~ '{_PERCEPTUAL_HASH_PATTERN}')"
)


training_image_fingerprints = sa.Table(
    "training_image_fingerprints",
    metadata,
    sa.Column(
        "training_image_id",
        sa.Uuid(),
        sa.ForeignKey(
            training_images.c.id,
            ondelete="CASCADE",
            # Shortened away from the convention's own rendering, which is
            # `fk_training_image_fingerprints_training_image_id_training_images`
            # — 64 bytes, one over PostgreSQL's limit. The two names on
            # `physical_copies` are shortened for the same reason and it is the
            # same failure: a truncated name reflects back differently from the
            # declared one, and `--autogenerate` then reports a drop-and-re-add
            # for ever.
            name="fk_training_image_fingerprints_image",
        ),
        primary_key=True,
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
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.CheckConstraint(_HASHES_ARE_HEX, name="hashes_are_lowercase_hex"),
    # `physical_copies`' certification pattern: half a fingerprint is not a
    # smaller fingerprint, it is a row whose distance to anything is undefined.
    sa.CheckConstraint(
        "(perceptual_hash IS NULL) = (perceptual_hash_rotated IS NULL)",
        name="both_hashes_or_neither",
    ),
    # No index. The pass reads this table whole, joins it to `training_images` by
    # primary key, and then compares every surviving pair against every other —
    # an index helps none of those. No trigger either: recomputing a fingerprint
    # under a new `hash_version` is an UPDATE, and the two mutable tables above
    # are the precedent.
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


# ---------------------------------------------------------------------------
# Spec §30's annotations — what is wrong with the card in the photograph
# ---------------------------------------------------------------------------
# The membership rule for `image_annotations`, composed from `tcg_domain.annotation`'s
# two mappings so the vocabulary is stated once. One constraint rather than
# three, because the three facts are not independent: `rough_cut` is an edge
# label and not a corner one, and a surface defect's position is its bounding
# box rather than a named region. Written as a disjunction over the kinds, it
# also makes `kind` itself a closed list, so no separate CHECK is needed for it.
#
# Sorted, deliberately: `LABELS_BY_KIND`'s values are frozensets, and an
# unsorted render would produce different SQL on different runs — which the
# migration could never be compared against.
def _kind_clause(kind: AnnotationKind) -> str:
    regions = REGIONS_BY_KIND[kind]
    placement = one_of("region", sorted(regions)) if regions else "region IS NULL"
    labels = one_of("label", sorted(LABELS_BY_KIND[kind]))
    return f"(kind = '{kind.value}' AND {placement} AND {labels})"


#: `IS TRUE`, and for the same reason :data:`_PROVENANCE_GATE` is written that
#: way — a `CHECK` whose expression evaluates to `NULL` **passes**. `region` is
#: nullable, so `region IN ('top_left', …)` is `NULL` rather than false for a
#: corner annotation that names no region, and the disjunction is then
#: `NULL OR false OR false`, which is `NULL`. Without this suffix the constraint
#: admits exactly the row it exists to refuse, and an integration test caught it
#: doing so. The wrap is on the whole expression rather than on the one
#: sub-clause that is nullable today, so a later nullable column joining the rule
#: cannot reopen the hole.
_KIND_REGION_AND_LABEL: Final = (
    "(" + " OR ".join(_kind_clause(kind) for kind in AnnotationKind) + ") IS TRUE"
)

#: Spec §17 requires a severity beside every defect, and the two labels that
#: assert *no* defect have nothing to rate. An equality between two booleans
#: rather than two implications, so neither direction can be relaxed on its own —
#: `physical_copies`' `certification_is_a_company_and_a_number` precedent.
_SEVERITY_PAIRING: Final = f"({one_of('label', sorted(NO_DEFECT_LABELS))}) = (severity IS NULL)"

#: An opaque identifier and nothing that could be a person. Spec §53's restraint
#: made structural: the grammar has no "@" in it, so a name or an email address
#: is not storable rather than merely discouraged.
_ANNOTATOR_ID_PATTERN: Final = "^[a-z0-9][a-z0-9_-]*$"

#: Coordinates are **fractions of the normalized artifact**, so a box has to fit
#: inside the unit square and has to have area. Paired with `num_nulls` below
#: rather than four separate null checks, and for the reason
#: `economic_configurations` gives about `cardinality`: the obvious spelling is
#: the one that is wrong on NULL.
#: Both axes, each admitting the NULL that says the axis has no measurable
#: border. A single constraint rather than one per axis, so `0.5` invented for a
#: borderless card is refused under one name.
_RATIOS_ARE_UNIT_INTERVALS: Final = (
    "(horizontal IS NULL OR (horizontal >= 0 AND horizontal <= 1)) "
    "AND (vertical IS NULL OR (vertical >= 0 AND vertical <= 1))"
)

#: The unit-square rule. The constraint's name still says "artifact" because it
#: predates #175 and renaming a CHECK is a drop-and-re-add on a frozen
#: migration's constant; the rule itself is frame-agnostic — a box is inside
#: the unit square of whichever representation its row names.
_BOX_LIES_INSIDE_THE_ARTIFACT: Final = (
    "bbox_x IS NULL OR ("
    "bbox_x >= 0 AND bbox_width > 0 AND bbox_x + bbox_width <= 1 "
    "AND bbox_y >= 0 AND bbox_height > 0 AND bbox_y + bbox_height <= 1)"
)

#: The two frames a coordinate can be a fraction of — #38's standardized
#: artifact, or the photograph as it was ingested. Spelled here rather than
#: imported from `datasets.annotation` (which owns the `NORMALIZED`/`ORIGINAL`
#: constants the bytes endpoint serves) because that module imports this one.
REPRESENTATIONS: Final = ("normalized", "original")

#: ADR 0010: the artifact cannot resolve §16's fine defect classes, and the
#: original photograph is the one route back — for *surface* annotations only.
#: Corners and edges were measured adequate against the artifact and stay
#: fractions of it. Both operands are NOT NULL, so no `IS TRUE` wrap is needed.
_ONLY_A_SURFACE_MARKS_THE_ORIGINAL: Final = "kind = 'surface' OR representation = 'normalized'"


image_annotations = sa.Table(
    "image_annotations",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "training_image_id",
        sa.Uuid(),
        sa.ForeignKey(
            training_images.c.id,
            ondelete="CASCADE",
            name="fk_image_annotations_training_image_id_training_images",
        ),
        nullable=False,
        comment=(
            "The photograph this is an annotation of. **CASCADE, with "
            "`training_image_fingerprints` and not with `dataset_members`**: an "
            "annotation describing bytes nobody holds any more is unusable, and RESTRICT "
            "would make it the reason a withdrawal ADR 0008 grants could not be honoured. "
            "A frozen version's claim on an image is already held by `dataset_members`."
        ),
    ),
    sa.Column(
        "kind",
        PRINTED,
        nullable=False,
        comment=(
            "Which of spec §30's three marker features this is — corner, edge or surface "
            "defect annotation. Not 'centering': §30's fourth is a measurement rather "
            "than a marker and lives in `centering_measurements`, which shares none of "
            "the columns below. No CHECK of its own, because "
            "ck_image_annotations_kind_region_and_label_agree is a disjunction over the kinds "
            "and closes this list as a side effect of closing the other two."
        ),
    ),
    sa.Column(
        "region",
        PRINTED,
        nullable=True,
        comment=(
            "Where on the card — one of §14's four corners or one of §15's four edges. "
            "**NULL exactly when `kind` is 'surface'**, because §16 names no positions: a "
            "surface defect's position is its bounding box. The side is deliberately not "
            "here; `training_images.side` already knows which face this is, and naming it "
            "twice would let the two disagree."
        ),
    ),
    sa.Column(
        "label",
        PRINTED,
        nullable=False,
        comment=(
            "What was found — §14's eight for a corner, §15's eight for an edge, §16's "
            "twelve for a surface. The three lists differ on purpose and are not one "
            "list: 'rough_cut' is a cutting defect an edge has and a corner has not, and "
            "§16 carries no 'clean' at all, because a surface with nothing wrong is a "
            "surface nobody annotated."
        ),
    ),
    sa.Column(
        "severity",
        PRINTED,
        nullable=True,
        comment=(
            "§17's severity — minor, moderate or severe. An **ordinal rather than a "
            "number in [0, 1]**: there is one annotator and no agreement study, so finer "
            "granularity would record a precision nobody could reproduce. NULL exactly "
            "when the label asserts no defect ('clean' found nothing to rate, 'unknown' "
            "could not rate what it found), and required otherwise."
        ),
    ),
    sa.Column(
        "confidence",
        sa.Double(),
        nullable=False,
        comment=(
            "§30's uncertainty, in [0, 1] — how sure the annotator is of this call. "
            "**NOT NULL and no server default**, which is what makes it one of §30's "
            "eleven rather than a nullable afterthought: an annotator who cannot tell "
            "whether a corner is soft records that, and a model trained on their "
            "confident guess is worse than one trained on their admission. The other half "
            "of the same rule is the 'unknown' label every vocabulary carries. "
            "`market_observations.confidence`'s shape, for the reason it gives: a "
            "mandatory field in an untyped bag quietly becomes optional."
        ),
    ),
    sa.Column(
        "bbox_x",
        sa.Double(),
        nullable=True,
        comment=(
            "§17's bounding box, as a **fraction of the representation this row names** "
            "rather than a pixel of it. For 'normalized' that is the 756x1056 artifact "
            "`ml/normalization` (#38) warps every image to, so the coordinate survives a "
            "retake and compares across cards; for 'original' it is the photograph "
            "itself, which ADR 0010 measured as the only frame that resolves §16's fine "
            "defect classes. A fraction rather than a pixel of either, so a resolution "
            "can change without rewriting every row — and so this module never has to "
            "import `ml/normalization`, which would put OpenCV in the API image. All four "
            "columns or none."
        ),
    ),
    sa.Column("bbox_y", sa.Double(), nullable=True, comment="As `bbox_x`, downward."),
    sa.Column("bbox_width", sa.Double(), nullable=True, comment="As `bbox_x`, and positive."),
    sa.Column("bbox_height", sa.Double(), nullable=True, comment="As `bbox_y`, and positive."),
    sa.Column(
        "polygon",
        postgresql.JSONB(),
        nullable=True,
        comment=(
            "§17's polygon — an array of [x, y] pairs in the same fractional space as "
            "the box above (the representation this row names), for a defect a rectangle "
            "describes badly. JSONB "
            "rather than a table of points, because nothing joins it and no query asks "
            "about one vertex. §17 says to capture spatial data from the beginning even "
            "though defect visualization is post-V1, and this is that: storable now, read "
            "by nothing yet."
        ),
    ),
    sa.Column(
        "representation",
        PRINTED,
        nullable=False,
        comment=(
            "Which frame this annotation's coordinates are fractions of — 'normalized' "
            "(#38's artifact) or 'original' (the photograph as ingested). ADR 0010 "
            "measured that the artifact cannot resolve §16's fine defect classes and "
            "named the original photograph the one route back, so #175 lets a *surface* "
            "annotation declare it — and only a surface, which "
            "ck_image_annotations_only_a_surface_marks_the_original holds. **NOT NULL on "
            "every row**: a marker with no box still names the frame the annotator "
            "judged the label against, and a 'scratch' call made off the 12 px/mm "
            "artifact is a weaker claim than one made off the original. No server "
            "default, for `confidence`'s reason: a representation nobody named must be "
            "refused rather than read as a choice."
        ),
    ),
    sa.Column(
        "metadata",
        postgresql.JSONB(),
        server_default=NO_METADATA,
        nullable=False,
        comment=(
            "§17's metadata — whatever the tool recorded that has no column of its own. "
            "Never the label, the severity or the confidence, each of which has a column "
            "and a constraint."
        ),
    ),
    sa.Column(
        "annotator_id",
        PRINTED,
        nullable=False,
        comment=(
            "§30's annotator ID — **an opaque identifier, never a name and never an "
            "email**. Spec §53's restraint applies to the people who label the corpus as "
            "much as to the people who use the product, and "
            "ck_image_annotations_annotator_id_is_opaque is that rule rather than a convention."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        comment=(
            "**§30's annotation timestamp, and there is deliberately no second column for "
            "it.** `training_images` carries both `acquired_at` and `created_at` because "
            "a photograph is taken long before it is ingested; an annotation *happens* "
            "when the tool writes the row, so an `annotated_at` beside this one would be "
            "one fact stored twice and free to disagree with itself."
        ),
    ),
    sa.CheckConstraint(_KIND_REGION_AND_LABEL, name="kind_region_and_label_agree"),
    sa.CheckConstraint(_SEVERITY_PAIRING, name="a_defect_carries_a_severity"),
    sa.CheckConstraint(
        f"severity IS NULL OR {one_of('severity', DefectSeverity)}",
        name="severity_is_a_known_severity",
    ),
    sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_is_a_unit_interval"),
    sa.CheckConstraint(
        "num_nulls(bbox_x, bbox_y, bbox_width, bbox_height) IN (0, 4)",
        name="bounding_box_is_whole_or_absent",
    ),
    sa.CheckConstraint(_BOX_LIES_INSIDE_THE_ARTIFACT, name="bounding_box_lies_inside_the_artifact"),
    sa.CheckConstraint(
        one_of("representation", REPRESENTATIONS), name="representation_is_a_known_representation"
    ),
    sa.CheckConstraint(
        _ONLY_A_SURFACE_MARKS_THE_ORIGINAL, name="only_a_surface_marks_the_original"
    ),
    sa.CheckConstraint(
        "polygon IS NULL OR jsonb_typeof(polygon) = 'array'", name="polygon_is_an_array"
    ),
    sa.CheckConstraint(f"annotator_id ~ '{_ANNOTATOR_ID_PATTERN}'", name="annotator_id_is_opaque"),
    # The annotation tool's only query — every annotation for the image on
    # screen — and the index the CASCADE check uses.
    sa.Index("ix_image_annotations_training_image_id", "training_image_id"),
    comment=(
        "One marker on one photograph — spec §30's corner, edge and surface defect "
        "annotation, carrying §17's spatial data and severity. **Append-only**: "
        "trg_image_annotations_immutable refuses an UPDATE, so a corrected annotation is a new "
        "row and a dataset version that referenced the old one keeps meaning what it "
        "meant. Nothing is unique per image, for the same reason: a surface has as many "
        "defects as it has, and the current view of a corner is the newest row for it. "
        "Named `image_annotations` rather than `annotations` because every module here "
        "carries `from __future__ import annotations`, and a table object called "
        "`annotations` shadows that binding wherever it is imported — the name is also "
        "the more accurate one, since this annotates an image."
    ),
)


centering_measurements = sa.Table(
    "centering_measurements",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "training_image_id",
        sa.Uuid(),
        sa.ForeignKey(
            training_images.c.id,
            ondelete="CASCADE",
            name="fk_centering_measurements_training_image_id_training_images",
        ),
        nullable=False,
        comment="The photograph measured. CASCADE, for the reason `image_annotations` gives.",
    ),
    sa.Column(
        "horizontal",
        sa.Double(),
        nullable=True,
        comment=(
            "The left border as a fraction of the two side borders together — "
            "left / (left + right). **0.5 is perfect centering**; below it the artwork "
            "sits left, above it right. Stated in one direction here because a ratio that "
            "means two things is worse than no ratio: '55/45' is ambiguous about which "
            "number is which, and §13 asks for ratios rather than qualitative labels "
            "without saying which way round. NULL where the axis cannot be measured — see "
            "the table comment."
        ),
    ),
    sa.Column(
        "vertical",
        sa.Double(),
        nullable=True,
        comment=(
            "The top border as a fraction of the two end borders together — "
            "top / (top + bottom). 0.5 is perfect, as above."
        ),
    ),
    sa.Column(
        "confidence",
        sa.Double(),
        nullable=False,
        comment=(
            "§30's uncertainty, in [0, 1] — required here exactly as on `image_annotations`, so "
            "that every annotation type can express it. A border read off a worn or "
            "glare-lit edge is a real measurement with a low confidence, and recording it "
            "at 1.0 would be the fabricated certainty spec §2.7 forbids."
        ),
    ),
    sa.Column(
        "notes",
        sa.Text(),
        nullable=True,
        comment=(
            "Free text — in practice, which of §21's awkward layouts this card is and "
            "what the annotator measured against. Not one of §30's eleven and not a "
            "vocabulary: template awareness is M7's model, and this is the human's note "
            "to it."
        ),
    ),
    sa.Column(
        "annotator_id",
        PRINTED,
        nullable=False,
        comment="§30's annotator ID, under the same grammar `image_annotations` uses.",
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        comment="§30's annotation timestamp, as on `image_annotations` and for the same reason.",
    ),
    # At least one axis, or the row records nothing. Not both required: see the
    # table comment.
    sa.CheckConstraint(
        "horizontal IS NOT NULL OR vertical IS NOT NULL", name="a_measurement_measures_something"
    ),
    sa.CheckConstraint(_RATIOS_ARE_UNIT_INTERVALS, name="ratios_are_unit_intervals"),
    sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_is_a_unit_interval"),
    sa.CheckConstraint(f"annotator_id ~ '{_ANNOTATOR_ID_PATTERN}'", name="annotator_id_is_opaque"),
    sa.Index("ix_centering_measurements_training_image_id", "training_image_id"),
    comment=(
        "Spec §30's centering measurements for one side of one card — §21's output, which "
        "§13 requires be ratios rather than qualitative labels. **Its own table rather "
        "than a fourth `image_annotations.kind`**: a measurement carries no label, no severity "
        "and no bounding box, and a marker carries no ratio, so one table would leave half "
        "of every row NULL by construction and would need two families of paired "
        "constraints to say which half. **Each axis is nullable on its own**, because §21 "
        "names full-art and borderless layouts outright: a card with no border on an axis "
        "has no ratio there, and inventing 0.5 for it is the confidently-wrong output §2.7 "
        "exists to forbid. Append-only, like `image_annotations`."
    ),
)


# ---------------------------------------------------------------------------
# The label the corpus is missing — spec §27, epic #9's "actual grade"
# ---------------------------------------------------------------------------
#: The four subgrades a slab can print, as column names. A tuple so the
#: constraints below and `test_datasets_tables.py` are built from one list
#: rather than four spellings of it.
SUBGRADE_COLUMNS: Final = (
    "subgrade_centering",
    "subgrade_corners",
    "subgrade_edges",
    "subgrade_surface",
)

#: All four, or none. `image_annotations`' bounding-box idiom: `num_nulls` is
#: what says "this group travels together" without four paired implications.
_SUBGRADES_ARE_A_SET: Final = f"num_nulls({', '.join(SUBGRADE_COLUMNS)}) IN (0, 4)"

#: The same grammar as `grade`, over each subgrade. One constraint rather than
#: four, so a failure names the rule rather than an arbitrary one of them.
_SUBGRADES_ARE_GRADES: Final = " AND ".join(
    f"({column} IS NULL OR {column} ~ '{_ISSUED_GRADE_PATTERN}')" for column in SUBGRADE_COLUMNS
)


grading_outcomes = sa.Table(
    "grading_outcomes",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "physical_copy_id",
        sa.Uuid(),
        sa.ForeignKey(physical_copies.c.id, ondelete="CASCADE"),
        nullable=False,
        comment=(
            "The card this outcome is about. **CASCADE, where `training_images` "
            "restricts**: a copy cannot be deleted while any image references it, so by "
            "the time one can be removed this row describes nothing — and RESTRICT here "
            "would make a spec §54 disposal, or a contributor withdrawal, fail for a "
            "reason nobody chose. `training_image_fingerprints`' argument."
        ),
    ),
    sa.Column(
        "grading_company",
        sa.Text(),
        nullable=False,
        comment=(
            "Which company issued this outcome. NOT NULL and CHECKed against the "
            "vocabulary, unlike `grading_rules.company`: this is data *about* a company, "
            "the `market_observations.grading_company` precedent."
        ),
    ),
    sa.Column(
        "certification_number",
        PRINTED,
        nullable=False,
        comment=(
            "The number printed on the slab that came back. Written onto the "
            "`physical_copies` row as well, where that row carries none, so §32's "
            "grouping key and the slab agree — the write `physical_copies` was left "
            "mutable for."
        ),
    ),
    sa.Column(
        "grade",
        PRINTED,
        nullable=True,
        comment=(
            "What was issued, as `tcg_domain.Grade` renders it. NULL where a designation "
            "was issued **in place of** a numeric grade, which is exactly PSA Authentic. "
            "Which grades a company can issue is checked in Python, never here."
        ),
    ),
    sa.Column(
        "designation",
        sa.Text(),
        nullable=True,
        comment=(
            "A label that is not a point on the scale — PSA Authentic, BGS Black Label, "
            "TAG Pristine 10. Its own column rather than a sixth value on the scale, "
            "which is what `tcg_grading_companies.companies`' ponytail note anticipated. "
            "BGS Black Label accompanies grade 10; PSA Authentic replaces a grade."
        ),
    ),
    *(
        sa.Column(
            column,
            PRINTED,
            nullable=True,
            comment=(
                f"BGS's {column.removeprefix('subgrade_')} subgrade, where the slab "
                "prints one. Recorded rather than read: V1 predicts an overall grade "
                "only (§24), and an unrecorded subgrade cannot be recovered once the "
                "card is sold. All four or none."
            ),
        )
        for column in SUBGRADE_COLUMNS
    ),
    sa.Column(
        "returned_at",
        sa.Date(),
        nullable=True,
        comment=(
            "When the slab came back. **NULL is meaningful**: ADR 0008's approved class 2 "
            "is a slab this project did not submit and whose outcome it still knows, and "
            "there is no return date to invent for one."
        ),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    # A submission carrying neither is not a submission. PSA issues Authentic in
    # place of a grade, so neither column can be NOT NULL on its own.
    sa.CheckConstraint(
        "grade IS NOT NULL OR designation IS NOT NULL",
        name="outcome_is_a_grade_or_a_designation",
    ),
    sa.CheckConstraint(
        one_of("grading_company", GradingCompany), name="grading_company_is_supported"
    ),
    sa.CheckConstraint(
        f"grade IS NULL OR grade ~ '{_ISSUED_GRADE_PATTERN}'", name="grade_is_an_issued_grade"
    ),
    sa.CheckConstraint(
        f"designation IS NULL OR {one_of('designation', Designation)}",
        name="designation_is_a_known_designation",
    ),
    sa.CheckConstraint(_SUBGRADES_ARE_A_SET, name="subgrades_are_four_or_none"),
    sa.CheckConstraint(_SUBGRADES_ARE_GRADES, name="subgrades_are_issued_grades"),
    sa.CheckConstraint(
        "btrim(certification_number) <> ''", name="certification_number_is_not_blank"
    ),
    # One slab is one outcome. This catches the CLI run twice; the *cross-copy*
    # case — one certification claimed by two physical cards — is caught by
    # `uq_physical_copies_certification`, through the write-back. Two
    # constraints, two different mistakes, two different refusals.
    sa.UniqueConstraint(
        "grading_company", "certification_number", name="uq_grading_outcomes_certification"
    ),
    sa.Index("ix_grading_outcomes_physical_copy_id", "physical_copy_id"),
    # **No immutability trigger, and that is a decision rather than an omission.**
    # An operator transcribes a grade and a certification number by hand off a
    # slab, and a typo has to be correctable — the same argument `physical_copies`
    # and `training_images` are mutable for, and ADR 0009 anticipates correcting
    # records by script. `test_datasets_tables.py` asserts the absence.
    #
    # **No `grading_rules_version` either.** Which published standard was in
    # force is `rules_in_force(company, returned_at)` over `grading_rules`, and
    # storing it would freeze today's reading: when a re-read reveals a standard
    # change with an earlier `effective_from`, the derived answer improves and a
    # stored one stays wrong. Spec §57's reproducibility record is a different
    # question and is M8's.
    comment=(
        "**One grading submission's outcome** — what one company issued for one physical "
        "card, once. Epic #9's acceptance criterion is ±1 *actual grade* and never "
        "says where the actual grade comes from; this is where. **One row per submission, "
        "never a column pair on `physical_copies`**: a copy can be graded by more than "
        "one company over its life, and a column pair would silently pick a winner. "
        "Deliberately not write-once, and it carries no grading rules version — see "
        "the comments above."
    ),
)


#: Every table this module contributes to the shared `MetaData`, in creation
#: order — `dataset_members` references two of the others, and
#: `training_image_fingerprints`, `image_annotations` and `centering_measurements` one
#: each.
TABLES: Final = (
    physical_copies,
    training_images,
    training_image_fingerprints,
    dataset_versions,
    dataset_members,
    image_annotations,
    centering_measurements,
    grading_outcomes,
)


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


# ---------------------------------------------------------------------------
# An annotation is appended, never edited
# ---------------------------------------------------------------------------
# A **second** function rather than a fourth caller of the one above, and the
# reason is the `HINT`. `dataset_records_are_immutable()` tells the reader to
# publish a new dataset version, which is the right instruction for
# `dataset_versions` and `dataset_members` and the wrong one for an annotator
# who mistyped a severity. The `MESSAGE` is `TG_TABLE_NAME`-driven and would have
# been fine; the hint is what an operator acts on at three in the morning, so it
# is worth twelve lines rather than being made generic enough to fit both.
#
# The rule itself is the same one #27 and #50 apply: a corrected annotation is a
# new row, because a dataset version that referenced the old one must keep
# meaning what it meant. `UPDATE` only, as everywhere else in this domain —
# spec §54's disposal and a withdrawn contributor both need rows removable, and
# the `CASCADE` from `training_images` is one of the paths that removes them.
_ANNOTATION_IMMUTABLE_FUNCTION: Final = _ddl(
    """
    CREATE OR REPLACE FUNCTION annotation_records_are_immutable()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE USING
            ERRCODE = 'restrict_violation',
            MESSAGE = TG_TABLE_NAME || ' is append-only: '
                      || TG_OP || ' was refused',
            HINT    = 'Record a correction as a new annotation rather than editing one.';
    END;
    $$;
    """
)

_IMAGE_ANNOTATIONS_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_image_annotations_immutable
    BEFORE UPDATE ON image_annotations
    FOR EACH ROW EXECUTE FUNCTION annotation_records_are_immutable();
    """
)

_CENTERING_TRIGGER: Final = _ddl(
    """
    CREATE TRIGGER trg_centering_measurements_immutable
    BEFORE UPDATE ON centering_measurements
    FOR EACH ROW EXECUTE FUNCTION annotation_records_are_immutable();
    """
)

_DROP_IMAGE_ANNOTATIONS_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_image_annotations_immutable ON image_annotations"
)

_DROP_CENTERING_TRIGGER: Final = _ddl(
    "DROP TRIGGER IF EXISTS trg_centering_measurements_immutable ON centering_measurements"
)

_DROP_ANNOTATION_IMMUTABLE_FUNCTION: Final = _ddl(
    "DROP FUNCTION IF EXISTS annotation_records_are_immutable()"
)

sa.event.listen(
    image_annotations,
    "after_create",
    _ANNOTATION_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
sa.event.listen(
    image_annotations, "after_create", _IMAGE_ANNOTATIONS_TRIGGER.execute_if(dialect="postgresql")
)
# The function is created before *each* trigger, not once before both.
# `dataset_members` can rely on `dataset_versions` having been created first,
# because a foreign key orders them; these two tables reference only
# `training_images` and so are ordered against each other alphabetically —
# `centering_measurements` first. `CREATE OR REPLACE` is idempotent, which makes
# the ordering stop mattering.
sa.event.listen(
    centering_measurements,
    "after_create",
    _ANNOTATION_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
sa.event.listen(
    centering_measurements, "after_create", _CENTERING_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(
    centering_measurements, "before_drop", _DROP_CENTERING_TRIGGER.execute_if(dialect="postgresql")
)
sa.event.listen(
    image_annotations,
    "before_drop",
    _DROP_IMAGE_ANNOTATIONS_TRIGGER.execute_if(dialect="postgresql"),
)
# Dropped last, and attached to `image_annotations` for the reason the pair above
# gives: `DROP TABLE` takes each trigger with it but never the function the two
# share. Note which function this is — dropping
# `dataset_records_are_immutable()` here would silently unguard
# `dataset_versions` and `dataset_members`.
sa.event.listen(
    image_annotations,
    "before_drop",
    _DROP_ANNOTATION_IMMUTABLE_FUNCTION.execute_if(dialect="postgresql"),
)
