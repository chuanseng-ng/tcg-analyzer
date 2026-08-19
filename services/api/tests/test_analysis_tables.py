"""Unit tests for the analysis table definitions.

Every test here runs without PostgreSQL: it inspects the `MetaData` and, where
the point is the SQL, the DDL SQLAlchemy compiles for the PostgreSQL dialect.
`test_analysis_schema.py` proves the same properties hold in a real database
after the migration has run; these prove they were declared on purpose.

One limit of this file is worth knowing. Alembic compares a check constraint's
*name*, not its *text*, so neither these tests nor the drift guard in
`test_catalog_schema.py` would notice a migration whose `IN` list had diverged
from `tcg_api.analysis.tables`. The parametrised "every value inserts" tests in
`test_analysis_schema.py` are what actually close that gap.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tcg_api.analysis.tables import TABLES, analyses, analysis_sessions, images
from tcg_api.tables import declared_tables, metadata
from tcg_domain.analysis import AnalysisStatus, ImageSide, QualityStatus, SessionStatus

SPEC_TABLES = ("analysis_sessions", "analyses", "images")


def ddl(table: sa.Table) -> str:
    return str(sa.schema.CreateTable(table).compile(dialect=postgresql.dialect()))


def check_constraint(table: sa.Table, name: str) -> str:
    """The rendered SQL of one named check constraint."""
    rendered = ddl(table)
    marker = f"CONSTRAINT ck_{table.name}_{name} CHECK "
    assert marker in rendered, f"{table.name} declares no check constraint {name!r}"
    return rendered.split(marker, 1)[1]


def foreign_key(table: sa.Table, name: str) -> sa.ForeignKeyConstraint:
    return next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint) and constraint.name == name
    )


# ---------------------------------------------------------------------------
# Spec §11 and §12 — the three tables and their columns
# ---------------------------------------------------------------------------
def test_the_analysis_domain_declares_the_three_tables() -> None:
    assert set(SPEC_TABLES) <= set(metadata.tables)
    assert tuple(table.name for table in TABLES) == SPEC_TABLES


def test_the_whole_schema_is_these_tables_and_the_catalogs() -> None:
    """A table autogenerate cannot reach is a table the next revision drops.

    `MetaData` is shared between the two domains, so "nothing sneaked in" can
    only be asserted once every table module has been executed — which is what
    `declared_tables()` is for, and the one place a new domain is registered.
    """
    declared = {table.name for table in declared_tables()}

    assert set(metadata.tables) == declared
    assert {table.name for table in TABLES} < declared


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            analysis_sessions,
            {
                "id",
                "anonymous_session_id",
                "created_at",
                "expires_at",
                "status",
                "application_version",
            },
        ),
        (
            analyses,
            {
                "id",
                "session_id",
                "card_id",
                "status",
                "created_at",
                "completed_at",
                "model_bundle_version",
                "market_snapshot_id",
                "economic_configuration_id",
            },
        ),
        (
            images,
            {
                "id",
                "analysis_id",
                "side",
                "original_uri",
                "normalized_uri",
                "width",
                "height",
                "mime_type",
                "sha256",
                "quality_score",
                "quality_status",
                "created_at",
            },
        ),
    ],
    ids=SPEC_TABLES,
)
def test_columns_match_the_specification(table: sa.Table, expected: set[str]) -> None:
    assert set(table.columns.keys()) == expected


@pytest.mark.parametrize("column", ["card_database_version", "grading_rules_version"])
def test_the_reproducibility_fields_this_issue_does_not_own_are_absent(column: str) -> None:
    """Spec §57 has seven fields; §12's column list has these two nowhere.

    They belong to the reproducibility-record issue, and must hold a published
    identifier resolved when the analysis ran — never a pointer to "current".
    Adding them here would ship two columns nothing knows how to fill.
    """
    assert column not in analyses.columns


# ---------------------------------------------------------------------------
# The vocabularies (spec §65, §19, §11, §52)
# ---------------------------------------------------------------------------
def test_all_nine_analysis_states_are_admitted() -> None:
    rendered = check_constraint(analyses, "status_is_a_known_analysis_state")

    for status in AnalysisStatus:
        assert f"'{status.value}'" in rendered


def test_queued_is_not_a_storable_state() -> None:
    """§65 answers `queued` from the run endpoint and then lists nine without it."""
    assert "'queued'" not in ddl(analyses)


def test_only_a_terminal_analysis_may_carry_a_completion_time() -> None:
    rendered = check_constraint(analyses, "completed_at_accompanies_a_terminal_status")

    assert "'completed'" in rendered
    assert "'failed'" in rendered
    assert "'analyzing'" not in rendered


def test_the_three_session_states_are_admitted() -> None:
    rendered = check_constraint(analysis_sessions, "status_is_a_known_session_state")

    for status in SessionStatus:
        assert f"'{status.value}'" in rendered


def test_guided_photography_needs_no_migration() -> None:
    """The issue's acceptance criterion, stated as DDL.

    Spec §52's flow captures angled and surface-lit images. V1 writes none of
    them, and the schema accepts all four from the first migration, because the
    alternative is altering a table full of user photographs.
    """
    rendered = check_constraint(images, "side_is_a_known_side")

    for side in ImageSide:
        assert f"'{side.value}'" in rendered
    for future in ("angled_front", "angled_back", "surface_front", "surface_back"):
        assert f"'{future}'" in rendered


def test_the_four_quality_statuses_are_admitted_and_so_is_none() -> None:
    """NULL until the quality gate has run, which is a later issue."""
    rendered = check_constraint(images, "quality_status_is_a_known_status")

    assert "quality_status IS NULL OR" in rendered
    for status in QualityStatus:
        assert f"'{status.value}'" in rendered
    assert images.columns["quality_status"].nullable is True


def test_no_vocabulary_becomes_a_postgresql_type() -> None:
    """A CHECK reverses with its table; `ALTER TYPE` has no DROP VALUE.

    There is no `CREATE TYPE` anywhere in this schema, and the value of that is
    entirely in `downgrade()`: a dropped table leaves a type behind exactly as
    it leaves a trigger's function behind.
    """
    for table in TABLES:
        compiled = ddl(table).lower()
        assert "create type" not in compiled
        assert "create extension" not in compiled


# ---------------------------------------------------------------------------
# The spine: what owns what
# ---------------------------------------------------------------------------
def test_a_session_owns_its_analyses() -> None:
    constraint = foreign_key(analyses, "fk_analyses_session_id_analysis_sessions")

    assert [element.target_fullname for element in constraint.elements] == ["analysis_sessions.id"]
    assert constraint.ondelete == "CASCADE"
    assert analyses.columns["session_id"].nullable is False


def test_an_analysis_owns_its_images() -> None:
    constraint = foreign_key(images, "fk_images_analysis_id_analyses")

    assert [element.target_fullname for element in constraint.elements] == ["analyses.id"]
    assert constraint.ondelete == "CASCADE"
    assert images.columns["analysis_id"].nullable is False


def test_the_card_is_unknown_until_the_user_confirms_it() -> None:
    """Nullable is the point: spec §20 makes confirmation a step, not a precondition."""
    constraint = foreign_key(analyses, "fk_analyses_card_id_cards")

    assert analyses.columns["card_id"].nullable is True
    assert [element.target_fullname for element in constraint.elements] == ["cards.id"]


def test_expiring_a_session_cannot_reach_the_catalog() -> None:
    """RESTRICT, not CASCADE and not SET NULL.

    CASCADE would let a catalog re-import delete a user's analysis. SET NULL is
    the subtle one: it would silently un-confirm the card the user chose, which
    turns a reproducibility record into a row that quietly changed its mind.
    """
    constraint = foreign_key(analyses, "fk_analyses_card_id_cards")

    assert constraint.ondelete == "RESTRICT"


# ---------------------------------------------------------------------------
# Privacy and retention (spec §53, §54)
# ---------------------------------------------------------------------------
def test_a_session_always_has_an_expiry() -> None:
    """A nullable `expires_at` makes "kept forever" representable by accident."""
    expires_at = analysis_sessions.columns["expires_at"]

    assert expires_at.nullable is False
    assert expires_at.server_default is None, (
        "the retention period is a policy that belongs where it can be reviewed, "
        "not in a column default"
    )


def test_a_session_cannot_expire_before_it_starts() -> None:
    assert "expires_at > created_at" in check_constraint(
        analysis_sessions, "expires_after_it_was_created"
    )


def test_the_anonymous_token_names_exactly_one_session() -> None:
    """It is the only thing separating one anonymous user's photographs from another's."""
    assert "UNIQUE (anonymous_session_id)" in ddl(analysis_sessions)


def test_the_retention_sweep_has_an_index() -> None:
    index = next(
        index
        for index in analysis_sessions.indexes
        if index.name == "ix_analysis_sessions_expires_at"
    )

    assert [column.name for column in index.columns] == ["expires_at"]


def test_the_cascade_target_is_indexed() -> None:
    """PostgreSQL does not index a foreign key's child column for you.

    Without this, deleting expired sessions in bulk scans `analyses` once per
    row deleted.
    """
    assert any(index.name == "ix_analyses_session_id" for index in analyses.indexes)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def test_one_image_per_side_per_analysis() -> None:
    """A retake replaces the front rather than appending a second one."""
    assert "UNIQUE (analysis_id, side)" in ddl(images)


def test_the_images_of_an_analysis_need_no_second_index() -> None:
    """`uq_images_analysis_id_side` leads with analysis_id, so it already serves that."""
    assert {index.name for index in images.indexes} == {"ix_images_sha256"}


def test_the_cache_key_is_indexed_and_not_unique() -> None:
    """The preprocessing cache is keyed on the digest.

    Not unique: two users photographing the same card is a coincidence, not a
    conflict, and deduplicating across users is explicitly not a product feature.
    """
    index = next(index for index in images.indexes if index.name == "ix_images_sha256")

    assert [column.name for column in index.columns] == ["sha256"]
    assert index.unique is False


def test_a_digest_is_sixty_four_lowercase_hex_characters() -> None:
    """One writer emitting uppercase would silently produce a second cache entry."""
    assert "^[0-9a-f]{64}$" in check_constraint(images, "sha256_is_lowercase_hex")
    assert images.columns["sha256"].nullable is False


def test_a_stored_image_knows_its_key_its_type_and_its_digest() -> None:
    """The row exists because bytes arrived and were validated."""
    for column in ("original_uri", "mime_type", "sha256"):
        assert images.columns[column].nullable is False, column


@pytest.mark.parametrize(
    "column",
    ["normalized_uri", "width", "height", "quality_score", "quality_status"],
)
def test_what_a_later_stage_computes_starts_empty(column: str) -> None:
    """Normalization writes the first three; the quality gate writes the last two."""
    assert images.columns[column].nullable is True


def test_a_zero_dimension_is_refused_but_an_absent_one_is_not() -> None:
    """Zero is not a smaller image; it is a division by zero waiting for centering."""
    rendered = check_constraint(images, "dimensions_are_positive")

    assert "width IS NULL OR width > 0" in rendered
    assert "height IS NULL OR height > 0" in rendered


def test_the_accepted_mime_types_are_not_a_schema_fact() -> None:
    """Adding HEIC must be an upload-policy change, not a migration."""
    assert "mime_type IN" not in ddl(images)


def test_the_quality_scale_is_left_to_the_gate() -> None:
    """Spec §19 fixes four statuses and says nothing about a scale.

    A bound invented here would be one the gate has to migrate away from.
    """
    assert "quality_score" not in check_constraint(images, "quality_status_is_a_known_status")
    assert "ck_images_quality_score" not in ddl(images)


# ---------------------------------------------------------------------------
# Collation (see `tcg_api.tables`)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("table", "column"),
    [
        (analysis_sessions, "anonymous_session_id"),
        (analysis_sessions, "application_version"),
        (analyses, "model_bundle_version"),
        (images, "sha256"),
    ],
    ids=["anonymous_session_id", "application_version", "model_bundle_version", "sha256"],
)
def test_compared_text_is_ordered_by_byte(table: sa.Table, column: str) -> None:
    """Identifiers and digests must compare the same way locally and in CI."""
    assert table.columns[column].type.collation == "C"
