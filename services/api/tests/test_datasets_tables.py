"""Unit tests for the dataset, provenance and membership tables.

Every test here runs without PostgreSQL: it inspects the `MetaData` and, where
the point is the SQL, the DDL SQLAlchemy compiles for the PostgreSQL dialect.
`test_datasets_schema.py` proves the same properties hold in a real database
after the migration has run; these prove they were declared on purpose.

Three properties are worth more than the rest:

* **The gate is spelled `IS TRUE`.** A `CHECK` whose expression evaluates to
  `NULL` passes, so `commercial_use_allowed AND derivative_use_allowed` would
  admit the unknown-provenance image ADR 0008 exists to refuse. The spelling is
  the constraint, and a test that only checked the constraint existed would not
  notice it being "simplified".
* **§29's nine fields are nine, on the image row.** ADR 0008 says outright that
  the per-copy identifier is *not* a tenth field, and ADR 0004 ruled out a
  competing provenance table.
* **Immutability stops at the version.** `physical_copies` and `training_images`
  must stay writable — a certification number arrives after the photographs do —
  so the absence of a trigger on them is asserted, not merely left alone.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from tcg_api.analysis.tables import images
from tcg_api.datasets.tables import (
    PROVENANCE_FIELDS,
    TABLES,
    centering_measurements,
    dataset_members,
    dataset_versions,
    image_annotations,
    physical_copies,
    training_image_fingerprints,
    training_images,
)
from tcg_api.table_registry import DECLARED_TABLES
from tcg_domain import DatasetSplit, ImageSide
from tcg_domain.annotation import LABELS_BY_KIND, REGIONS_BY_KIND

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = REPO_ROOT / "database" / "migrations" / "versions"
MIGRATION = VERSIONS / "20260828_add_the_dataset_and_provenance_schema.py"
#: #155's fingerprints landed in a revision of their own, so the generic source
#: search below reads both files: a CHECK declared in `tables.py` is in *one* of
#: them, and which one is not the drift this test is about.
FINGERPRINTS_MIGRATION = VERSIONS / "20260829_add_the_training_image_fingerprints.py"
#: #158's annotations landed in a third revision, read by the same search.
ANNOTATION_MIGRATION = VERSIONS / "20260829_add_the_annotation_schema.py"

#: Spec §29's list, verbatim apart from `source_url/reference`, which the
#: specification spells with a slash and no column may.
SECTION_29_FIELDS = (
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


#: The three CHECKs the migration builds from a named constant rather than
#: writing out, because the reasoning for their spelling belongs beside them.
#: They are excluded from the generic source search below and asserted by value
#: instead, which is stronger — `test_market_tables.py` does the same for the
#: literals its own migration hoists.
COMPOSED_CHECKS = (
    "ck_training_images_provenance_permits_training",
    "ck_training_images_sha256_is_lowercase_hex",
    "ck_dataset_versions_version_is_an_explicit_identifier",
    "ck_training_image_fingerprints_hashes_are_lowercase_hex",
    "ck_image_annotations_kind_region_and_label_agree",
    "ck_image_annotations_bounding_box_lies_inside_the_artifact",
    "ck_image_annotations_annotator_id_is_opaque",
    "ck_centering_measurements_ratios_are_unit_intervals",
    "ck_centering_measurements_annotator_id_is_opaque",
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (MIGRATION, FINGERPRINTS_MIGRATION, ANNOTATION_MIGRATION)
    )


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    """The migration, imported, so its constants can be compared by value.

    A migration is a snapshot of what was applied and is deliberately not
    importable from `tables.py`; loading it here is how the two copies are held
    to each other without either reading the other.
    """
    return _imported("datasets_migration", MIGRATION)


@pytest.fixture(scope="module")
def fingerprints_migration() -> ModuleType:
    """#155's revision, for the same reason and by the same means."""
    return _imported("fingerprints_migration", FINGERPRINTS_MIGRATION)


@pytest.fixture(scope="module")
def annotation_migration() -> ModuleType:
    """#158's revision, likewise."""
    return _imported("annotation_migration", ANNOTATION_MIGRATION)


def _imported(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ddl(table: sa.Table) -> str:
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_the_domain_is_registered() -> None:
    """A domain the registry does not import is one autogenerate proposes dropping."""
    assert set(TABLES) <= set(DECLARED_TABLES)


def test_the_seven_tables_are_declared_on_the_shared_metadata() -> None:
    assert {table.name for table in TABLES} == {
        "physical_copies",
        "training_images",
        "training_image_fingerprints",
        "dataset_versions",
        "dataset_members",
        "image_annotations",
        "centering_measurements",
    }


# ---------------------------------------------------------------------------
# Spec §29's provenance, on the image row
# ---------------------------------------------------------------------------


def test_every_section_29_field_is_a_column_on_the_image() -> None:
    """ADR 0004 ruled out a competing provenance table; §29 says *every image needs*."""
    assert set(SECTION_29_FIELDS) <= set(training_images.c.keys())


def test_the_provenance_fields_are_nine_and_no_more() -> None:
    """ADR 0008: the per-copy identifier is not a tenth §29 field.

    `physical_copies` is where the copy is identified, and that is a gap in the
    *dataset* schema rather than in §29's list.
    """
    assert PROVENANCE_FIELDS == SECTION_29_FIELDS
    assert len(PROVENANCE_FIELDS) == 9


def test_the_gate_refuses_an_unknown_answer_rather_than_passing_on_null() -> None:
    """The single most important line in the domain.

    `NULL AND true` is `NULL`, and a `CHECK` **passes** on `NULL`. Written as a
    bare `commercial_use_allowed AND derivative_use_allowed`, ADR 0008's gate
    would admit exactly the row it exists to refuse. This asserts the spelling,
    not merely the constraint's existence.
    """
    gate = one_check(training_images, "provenance_permits_training")

    assert "commercial_use_allowed IS TRUE" in gate
    assert "derivative_use_allowed IS TRUE" in gate
    assert "license IS NOT NULL" in gate
    assert "btrim(license) <> ''" in gate


def test_the_gate_reads_columns_that_are_nullable() -> None:
    """One refusal with one name, rather than three constraints with three messages."""
    assert training_images.c.commercial_use_allowed.nullable
    assert training_images.c.derivative_use_allowed.nullable
    assert training_images.c.license.nullable


def test_no_rights_column_carries_a_server_default() -> None:
    """A boolean that reads true because nobody wanted a null is the failure ADR 0008 names."""
    for field in ("commercial_use_allowed", "derivative_use_allowed", "redistribution_allowed"):
        assert training_images.c[field].server_default is None


def test_redistribution_is_recorded_and_never_gated() -> None:
    """ADR 0008 makes it false on all four approved sources; the column records that.

    A CHECK on its *value* would make it a switch somebody could later waive by
    migration, and NOT NULL is what stops it being silently absent instead.
    """
    assert not training_images.c.redistribution_allowed.nullable
    assert "redistribution_allowed" not in one_check(training_images, "provenance_permits_training")
    assert not [
        constraint
        for constraint in training_images.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and "redistribution_allowed" in str(constraint.sqltext)
    ]


def test_source_carries_no_membership_check() -> None:
    """`grading_rules.company`'s precedent: a fifth approved source costs an ADR, not a migration.

    Deliberately the inverse assertion — the allow-list is enforced in the
    ingestion path, where it changes, and the rights are enforced in the schema,
    where they do not.
    """
    assert "first_party" not in ddl(training_images)
    assert "product_upload" not in ddl(training_images)


def test_no_foreign_key_reaches_the_analysis_spine() -> None:
    """A consented upload's `analysis_id` travels as text, deliberately.

    Spec §54 deletes the analysis on schedule and the training image outlives it,
    so a key here would make retention and the corpus fight each other.
    """
    referenced = {key.column.table.name for key in training_images.foreign_keys}

    assert referenced == {"physical_copies", "cards"}


# ---------------------------------------------------------------------------
# Spec §32's grouping keys
# ---------------------------------------------------------------------------


def test_the_copy_is_identified_without_the_catalog() -> None:
    """Two copies of one Charizard share a `card_id` and must be splittable apart."""
    assert "card_id" not in physical_copies.c
    assert not physical_copies.foreign_keys


def test_an_unidentified_copy_is_representable() -> None:
    """Approved class 4 has no per-copy identifier, and §32 lists `source` as a key.

    A grouping key that is honestly coarse beats one that is confidently wrong,
    so NULL here is the answer rather than a gap.
    """
    assert training_images.c.physical_copy_id.nullable


def test_the_digest_is_unique_here_and_not_on_analysis_images() -> None:
    """The exact-duplicate half of ADR 0009's deduplication, and only that half.

    `images.sha256` is deliberately not unique — the same photograph uploaded to
    two analyses is two images — where the same photograph ingested twice is one
    training image.
    """
    assert {"sha256"} in [
        set(c.columns.keys())
        for c in training_images.constraints
        if isinstance(c, sa.UniqueConstraint)
    ]
    assert not [
        constraint
        for constraint in images.constraints
        if isinstance(constraint, sa.UniqueConstraint)
        and set(constraint.columns.keys()) == {"sha256"}
    ]


def test_the_side_vocabulary_is_the_one_analysis_images_use() -> None:
    """A training corpus and an uploaded analysis must not spell 'front' two ways."""
    for side in ImageSide:
        assert f"'{side.value}'" in one_check(training_images, "side_is_a_known_side")


# ---------------------------------------------------------------------------
# Spec §31's versions
# ---------------------------------------------------------------------------


def test_a_version_cannot_be_a_pointer() -> None:
    """§31 forbids a model referencing `/latest/`; the grammar makes it unstorable."""
    grammar = one_check(dataset_versions, "version_is_an_explicit_identifier")

    assert "-v" in grammar
    assert r"\d+\.\d+\.\d+" in grammar


def test_the_seed_is_stored_and_the_proportions_are_not() -> None:
    """A split that cannot be reproduced makes a version reproducible in name only.

    The achieved proportions are a count over `dataset_members`; a stored copy is
    a second answer that can drift from the first.
    """
    assert "split_seed" in dataset_versions.c
    assert not [column for column in dataset_versions.c if "proportion" in column.name]


def test_membership_is_stored_rather_than_derived() -> None:
    """The deliberate difference from `market_snapshots` (#51).

    A snapshot's membership is derivable from a cut-line on `created_at`; a
    train/validation/test assignment is a decision and is derivable from nothing.
    """
    assert set(dataset_members.primary_key.columns.keys()) == {
        "dataset_version_id",
        "training_image_id",
    }
    for split in DatasetSplit:
        assert f"'{split.value}'" in one_check(dataset_members, "split_is_a_known_split")


def test_a_frozen_version_cannot_lose_an_image_it_named() -> None:
    """ADR 0008 grants retention after a contributor withdraws, because §31 needs it."""
    by_column = {
        next(iter(key.parent.name for key in [k])): k.ondelete for k in dataset_members.foreign_keys
    }

    assert by_column["training_image_id"] == "RESTRICT"
    assert by_column["dataset_version_id"] == "CASCADE"


# ---------------------------------------------------------------------------
# The migration, and the drift the drift guard cannot see
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", TABLES, ids=lambda table: str(table.name))
def test_the_migration_and_the_declaration_agree_on_every_check(
    table: sa.Table, migration_source: str
) -> None:
    """Alembic compares a check's *name* but never its *text*.

    So an `IN` list or a gate expression that had drifted would pass the schema
    drift guard in `test_catalog_schema.py`. This is what catches it.
    """
    prefix = f"ck_{table.name}_"
    checks = [
        constraint for constraint in table.constraints if isinstance(constraint, sa.CheckConstraint)
    ]

    assert checks, f"{table.name} declares no CHECK"
    for constraint in checks:
        assert str(constraint.name).removeprefix(prefix) in migration_source
        if str(constraint.name) in COMPOSED_CHECKS:
            continue
        assert str(constraint.sqltext) in migration_source


def test_the_migration_and_the_declaration_share_the_gate(migration: ModuleType) -> None:
    """The three composed CHECKs, compared by value rather than by substring.

    This is the pair `test_the_migration_and_the_declaration_agree_on_every_check`
    skips, and it is the more important half: the gate is what ADR 0009 chose a
    database for, and a migration whose gate had been "simplified" back to a bare
    `commercial_use_allowed AND derivative_use_allowed` would apply a constraint
    that passes on `NULL`.
    """
    assert one_check(training_images, "provenance_permits_training") == migration.PROVENANCE_GATE
    assert "IS TRUE" in migration.PROVENANCE_GATE
    assert f"sha256 ~ '{migration.SHA256_PATTERN}'" == one_check(
        training_images, "sha256_is_lowercase_hex"
    )
    assert f"version ~ '{migration.VERSION_PATTERN}'" == one_check(
        dataset_versions, "version_is_an_explicit_identifier"
    )


def test_the_migration_creates_both_triggers_and_one_function() -> None:
    """#153's revision alone, deliberately not the concatenated source.

    #158 adds a second immutability function in a revision of its own, and the
    claim here is that *this* revision declares one — two would mean two
    refusals with two messages for one rule.
    """
    source = MIGRATION.read_text(encoding="utf-8")

    assert source.count("CREATE OR REPLACE FUNCTION") == 1
    assert "dataset_records_are_immutable" in source
    assert "trg_dataset_versions_immutable" in source
    assert "trg_dataset_members_immutable" in source


def test_the_migration_freezes_the_version_and_not_the_corpus(migration_source: str) -> None:
    """A certification number arrives after the photographs do — #150's whole point.

    The absence of a trigger on the two mutable tables is a decision, so it is
    asserted rather than left to be noticed.
    """
    assert "trg_training_images_immutable" not in migration_source
    assert "trg_physical_copies_immutable" not in migration_source


def test_the_trigger_guards_update_and_leaves_delete_open(migration_source: str) -> None:
    """Spec §54's disposal and a withdrawn contributor both need rows removable."""
    assert "BEFORE UPDATE ON dataset_versions" in migration_source
    assert "BEFORE UPDATE ON dataset_members" in migration_source
    assert "BEFORE UPDATE OR DELETE" not in migration_source


def test_the_migration_inserts_no_rows(migration_source: str) -> None:
    """ADR 0008 admits an image only through the ingestion path a later issue builds."""
    assert "op.bulk_insert" not in migration_source
    assert "INSERT INTO" not in migration_source


def test_every_constraint_name_fits_postgres_identifier_limit() -> None:
    """63 bytes, and `ck_physical_copies_` already spends 19 of them.

    SQLAlchemy renders an over-long name as a truncated stem plus a hash, and the
    reflected name then differs from the declared one — which
    `alembic revision --autogenerate` reports as a constraint dropped and
    re-added on **every** run, for ever.
    """
    for table in TABLES:
        for constraint in (*table.constraints, *table.indexes):
            if constraint.name:
                assert len(str(constraint.name).encode()) <= 63, constraint.name


def one_check(table: sa.Table, short_name: str) -> str:
    """The text of one named CHECK, by the short name the convention prefixes."""
    full = f"ck_{table.name}_{short_name}"
    for constraint in table.constraints:
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == full:
            return str(constraint.sqltext)
    raise AssertionError(f"{full} is not declared on {table.name}")


# ---------------------------------------------------------------------------
# #155's fingerprints, and what is deliberately not stored beside them
# ---------------------------------------------------------------------------


def test_a_fingerprint_is_keyed_on_the_image_and_nothing_else() -> None:
    """A derived row has no identity of its own to be keyed on."""
    key = training_image_fingerprints.primary_key.columns

    assert [column.name for column in key] == ["training_image_id"]


def test_a_fingerprint_cascades_where_every_other_key_into_the_image_restricts() -> None:
    """A hash must never be the reason a training image cannot be removed.

    `dataset_members` restricts because §31 means a version cannot un-include an
    image; a fingerprint means nothing without the bytes it describes, so it goes
    with them. The contrast is the assertion.
    """
    (fingerprint_key,) = training_image_fingerprints.c.training_image_id.foreign_keys
    (member_key,) = dataset_members.c.training_image_id.foreign_keys

    assert fingerprint_key.ondelete == "CASCADE"
    assert member_key.ondelete == "RESTRICT"


def test_a_fingerprint_may_record_that_no_card_was_found() -> None:
    """Both hashes nullable, together — a row rather than a gap.

    An image the detector finds nothing in still gets a row, under the version
    that examined it, so the next pass does not decode those bytes again.
    """
    assert training_image_fingerprints.c.perceptual_hash.nullable
    assert training_image_fingerprints.c.perceptual_hash_rotated.nullable
    assert not training_image_fingerprints.c.hash_version.nullable
    assert one_check(training_image_fingerprints, "both_hashes_or_neither")


def test_no_pair_and_no_group_is_stored() -> None:
    """The decision this table exists to embody, asserted as an absence.

    A duplicate relationship is a pure function of two hashes and a threshold,
    and the threshold is not stored — so it is derived when asked, exactly as
    `market_snapshots` derives its membership from a cut-line. A stored pair is a
    second answer that drifts from the first the moment the number moves.
    """
    for table in TABLES:
        assert not str(table.name).endswith(("_duplicates", "_pairs", "_groups")), table.name

    columns = {column.name for column in training_image_fingerprints.columns}
    assert not columns & {"distance", "threshold", "duplicate_of", "duplicate_group_id"}


def test_the_stored_version_names_the_hash_and_not_the_threshold() -> None:
    """Moving the threshold invalidates no row, so it is not part of what is stored."""
    from tcg_api.datasets.deduplication import HASH_VERSION
    from tcg_api.datasets.fingerprints import DHASH_VERSION, NEAR_DUPLICATE_DISTANCE

    assert HASH_VERSION.startswith(DHASH_VERSION)
    assert str(NEAR_DUPLICATE_DISTANCE) not in HASH_VERSION.split("+")


def test_the_fingerprint_migration_and_the_declaration_share_the_hash_grammar(
    fingerprints_migration: ModuleType,
) -> None:
    """The composed CHECK the generic source search skips, compared by value.

    Both hash columns share one grammar and therefore one constraint, built from
    one pattern — so the width of the hash is stated in exactly one place per
    copy, and this holds the two copies together.
    """
    assert one_check(training_image_fingerprints, "hashes_are_lowercase_hex") == (
        fingerprints_migration.HASHES_ARE_HEX
    )
    assert fingerprints_migration.PERCEPTUAL_HASH_PATTERN == "^[0-9a-f]{16}$"
    assert fingerprints_migration.PERCEPTUAL_HASH_PATTERN in fingerprints_migration.HASHES_ARE_HEX


def test_the_fingerprint_migration_creates_no_trigger_and_drops_no_function() -> None:
    """Recomputing under a new version is an UPDATE, so no trigger — and the
    shared function still guards the two tables that do carry one.

    A `DROP FUNCTION` copied into this revision's `downgrade()` would silently
    unguard `dataset_versions` and `dataset_members`; `test_migrations.py` proves
    it against a real database, and this catches it by reading.
    """
    source = FINGERPRINTS_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION" not in source
    assert "trg_training_image_fingerprints" not in source
    assert "DROP FUNCTION" not in source
    assert "op.bulk_insert" not in source


def test_the_fingerprints_table_carries_no_index() -> None:
    """The pass reads it whole, joins by primary key and compares every pair."""
    assert training_image_fingerprints.indexes == set()


# ---------------------------------------------------------------------------
# #158's annotations, and the two shapes §30 asks for
# ---------------------------------------------------------------------------


def test_a_measurement_and_a_marker_are_two_tables() -> None:
    """§30 lists both, and they share no field but the annotator and the time.

    A fourth `image_annotations.kind` would leave a label, a severity and a bounding
    box NULL on every centering row and two ratios NULL on every other one. The
    assertion is the absence of the columns each would have borrowed.
    """
    marker = set(image_annotations.c.keys())
    measurement = set(centering_measurements.c.keys())

    assert not marker & {"horizontal", "vertical"}
    assert not measurement & {"kind", "region", "label", "severity"}
    assert "centering" not in one_check(image_annotations, "kind_region_and_label_agree")


def test_every_kind_carries_its_own_labels_and_its_own_regions() -> None:
    """One constraint, composed from the domain's mappings — so a label added to
    one of the specification's lists cannot become one the database refuses.

    Written out here rather than derived from `LABELS_BY_KIND`, because a test
    that reads its expectation from the code under test proves only that the
    code equals itself.
    """
    rule = one_check(image_annotations, "kind_region_and_label_agree")

    assert "kind = 'corner'" in rule
    assert "'rough_cut'" in rule
    # `rough_cut` is an edge label and not a corner one; the two lists are not
    # one list, and this is what stops a corner being annotated with it.
    corner_clause, _, rest = rule.partition("OR (kind = 'edge'")
    assert "'rough_cut'" not in corner_clause
    assert "'rounding'" not in rest.partition("OR (kind = 'surface'")[0]


def test_the_vocabulary_rule_refuses_a_missing_region_rather_than_passing_on_null() -> None:
    """The `IS TRUE` trap again, in a second constraint and for a second reason.

    `region` is nullable, so `region IN ('top_left', ...)` is `NULL` — not false —
    for a corner annotation that names no region, and the disjunction is then
    `NULL OR false OR false`, which is `NULL`, and a `CHECK` **passes** on `NULL`.
    An integration test caught this constraint admitting exactly the row it
    exists to refuse; this asserts the spelling that fixed it, as
    `test_the_gate_refuses_an_unknown_answer_rather_than_passing_on_null` does
    for ADR 0008's gate.
    """
    assert one_check(image_annotations, "kind_region_and_label_agree").endswith(") IS TRUE")


def test_a_surface_defect_is_placed_by_its_box_and_not_by_a_region() -> None:
    """§16 names no positions, so `region` is NULL exactly for that kind."""
    rule = one_check(image_annotations, "kind_region_and_label_agree")

    assert "kind = 'surface' AND region IS NULL" in rule
    assert image_annotations.c.region.nullable


def test_a_defect_without_a_severity_is_not_storable() -> None:
    """§17 requires a severity beside every defect.

    An equality between two booleans rather than two implications, so neither
    direction can be relaxed on its own: `clean` with a severity is as refused
    as `chipping` without one.
    """
    assert one_check(image_annotations, "a_defect_carries_a_severity") == (
        "(label IN ('clean', 'unknown')) = (severity IS NULL)"
    )
    assert image_annotations.c.severity.nullable


def test_uncertainty_is_required_on_every_annotation_type() -> None:
    """§30's eleventh field, and the acceptance criterion in as many words.

    NOT NULL on both tables and no server default on either — a confidence that
    reads 1.0 because nobody supplied one is the fabricated certainty §2.7
    forbids, and it is exactly what a default would produce.
    """
    for table in (image_annotations, centering_measurements):
        assert not table.c.confidence.nullable
        assert table.c.confidence.server_default is None
        assert one_check(table, "confidence_is_a_unit_interval")


def test_an_axis_nobody_could_measure_is_null_rather_than_a_half() -> None:
    """§21 names full-art and borderless layouts, which have no border to measure.

    Each axis is nullable on its own, and a row measuring neither is refused —
    that row would record nothing but an annotator and a time.
    """
    assert centering_measurements.c.horizontal.nullable
    assert centering_measurements.c.vertical.nullable
    assert one_check(centering_measurements, "a_measurement_measures_something") == (
        "horizontal IS NOT NULL OR vertical IS NOT NULL"
    )


def test_the_ratios_are_numbers_and_not_labels() -> None:
    """§13: "represented as ratios/percentages, not simply qualitative labels"."""
    for column in (centering_measurements.c.horizontal, centering_measurements.c.vertical):
        assert isinstance(column.type, sa.Double)


def test_coordinates_are_fractions_of_the_artifact_and_name_no_resolution() -> None:
    """The acceptance criterion: coordinates are in the normalized artifact's space.

    Fractions rather than pixels of it, which is what keeps `tables.py` free of
    `ml/normalization` — importing that for the two dimensions would put OpenCV
    in the API image, which `test_import_purity.py` forbids. So the numbers 756
    and 1056 must appear nowhere in this table's DDL.
    """
    box = one_check(image_annotations, "bounding_box_lies_inside_the_artifact")

    assert "bbox_x + bbox_width <= 1" in box
    assert "bbox_y + bbox_height <= 1" in box
    assert "bbox_width > 0" in box
    assert "756" not in ddl(image_annotations)
    assert "1056" not in ddl(image_annotations)


def test_a_partial_bounding_box_is_not_storable() -> None:
    """Three of four coordinates is not a smaller box, it is a box nobody can draw.

    `num_nulls` rather than four `IS NULL` comparisons, and the same trap
    `economic_configurations` documents for `cardinality`.
    """
    assert one_check(image_annotations, "bounding_box_is_whole_or_absent") == (
        "num_nulls(bbox_x, bbox_y, bbox_width, bbox_height) IN (0, 4)"
    )


def test_spatial_data_is_captured_although_nothing_reads_it() -> None:
    """§17: capture spatial data from the beginning, even though visualization is post-V1."""
    assert {"bbox_x", "bbox_y", "bbox_width", "bbox_height", "polygon"} <= set(
        image_annotations.c.keys()
    )
    assert one_check(image_annotations, "polygon_is_an_array")


def test_the_annotator_is_opaque_rather_than_a_person() -> None:
    """Spec §53's restraint, made structural rather than documented.

    An identifier under this grammar cannot contain '@' or a space, so a name or
    an email address is not storable — which is a different claim from a comment
    asking the next person not to store one.
    """
    grammar = "^[a-z0-9][a-z0-9_-]*$"
    for table in (image_annotations, centering_measurements):
        assert one_check(table, "annotator_id_is_opaque") == f"annotator_id ~ '{grammar}'"

    assert re.fullmatch(grammar, "annotator-1")
    assert not re.fullmatch(grammar, "someone@example.com")
    assert not re.fullmatch(grammar, "Ada Lovelace")


def test_the_annotation_timestamp_is_created_at_and_is_not_stored_twice() -> None:
    """§30's eleventh feature has a home, and only one.

    `training_images` needs both `acquired_at` and `created_at` because a
    photograph is taken long before it is ingested. An annotation happens when
    the row is written, so a second column would be one fact free to disagree
    with itself.
    """
    for table in (image_annotations, centering_measurements):
        assert "created_at" in table.c
        assert table.c.created_at.server_default is not None
        assert "annotated_at" not in table.c
        assert "acquired_at" not in table.c


def test_the_side_is_the_images_and_is_not_repeated_on_the_annotation() -> None:
    """§14 lists eight corners; four of them are `training_images.side`.

    Naming the side here as well would let an annotation claim a face its image
    does not show, and §30's front/back is a viewer control rather than a field.
    """
    for table in (image_annotations, centering_measurements):
        assert "side" not in table.c


def test_an_annotation_never_blocks_the_removal_of_what_it_describes() -> None:
    """CASCADE, with the fingerprint and not with the dataset member.

    ADR 0008 grants retention after a withdrawal precisely because §31 needs it;
    an annotation is not a version's claim, and RESTRICT here would make it one.
    """
    for table in (image_annotations, centering_measurements):
        (key,) = table.c.training_image_id.foreign_keys
        assert key.ondelete == "CASCADE"
        assert key.column.table.name == "training_images"


def test_nothing_here_reaches_the_analysis_or_the_catalog() -> None:
    """A training image is the only thing an annotation is about."""
    for table in (image_annotations, centering_measurements):
        assert {key.column.table.name for key in table.foreign_keys} == {"training_images"}


def test_an_annotation_carries_no_grade_and_no_condition_score() -> None:
    """M7 defines the neutral condition representation and derives it *from* these.

    #37's `predict_grade` parameter is typed `object` for exactly that reason, so
    a grade or a condition score appearing here would be this issue answering
    M7's question. The inverse assertion is the guard.
    """
    columns = set(image_annotations.c.keys()) | set(centering_measurements.c.keys())

    assert not columns & {"grade", "condition", "condition_score", "grading_company"}


def test_the_annotation_migration_creates_its_own_function_and_drops_only_that() -> None:
    """Two functions now, and the wrong `DROP FUNCTION` would unguard two tables.

    `dataset_records_are_immutable()` still guards `dataset_versions` and
    `dataset_members`; a copied downgrade that dropped it here would leave both
    editable with nothing failing.
    """
    source = ANNOTATION_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION annotation_records_are_immutable" in source
    assert "DROP FUNCTION IF EXISTS annotation_records_are_immutable()" in source
    assert "DROP FUNCTION IF EXISTS dataset_records_are_immutable" not in source
    assert "op.bulk_insert" not in source
    assert "INSERT INTO" not in source


def test_the_annotation_trigger_guards_update_and_leaves_delete_open() -> None:
    """The CASCADE above is a DELETE, so a trigger on DELETE would break it."""
    source = ANNOTATION_MIGRATION.read_text(encoding="utf-8")

    assert "BEFORE UPDATE ON image_annotations" in source
    assert "BEFORE UPDATE ON centering_measurements" in source
    assert "BEFORE UPDATE OR DELETE" not in source


def test_the_annotation_migration_and_the_declaration_share_the_composed_checks(
    annotation_migration: ModuleType,
) -> None:
    """The five the generic source search skips, compared by value.

    The vocabulary one is the important one: it carries §14's, §15's and §16's
    three lists, and Alembic compares a check's name but never its text — so a
    migration whose label list had drifted from `tcg_domain.annotation` would
    pass every other guard in this repository.
    """
    assert one_check(image_annotations, "kind_region_and_label_agree") == (
        annotation_migration.KIND_REGION_AND_LABEL
    )
    assert one_check(image_annotations, "bounding_box_lies_inside_the_artifact") == (
        annotation_migration.BOX_LIES_INSIDE_THE_ARTIFACT
    )
    assert one_check(centering_measurements, "ratios_are_unit_intervals") == (
        annotation_migration.RATIOS_ARE_UNIT_INTERVALS
    )
    for table in (image_annotations, centering_measurements):
        assert one_check(table, "annotator_id_is_opaque") == (
            f"annotator_id ~ '{annotation_migration.ANNOTATOR_ID_PATTERN}'"
        )


def test_the_migration_spells_the_vocabulary_the_domain_spells() -> None:
    """The composed CHECK is built from `LABELS_BY_KIND`, so this closes the loop.

    `test_domain_annotation.py` holds the mappings to the specification by value;
    this holds the constraint to the mappings, and the test above holds the
    migration to the constraint.
    """
    rule = one_check(image_annotations, "kind_region_and_label_agree")

    for kind, labels in LABELS_BY_KIND.items():
        clause = next(part for part in rule.split(" OR (kind = ") if f"'{kind.value}'" in part)
        for label in labels:
            assert f"'{label}'" in clause
        for region in REGIONS_BY_KIND[kind]:
            assert f"'{region}'" in clause
