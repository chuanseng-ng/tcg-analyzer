"""Integration tests for the analysis schema as PostgreSQL actually built it.

`test_analysis_tables.py` proves the schema was *declared* correctly. This
proves the migration built what was declared, and — more usefully — the
guarantees only a real database can demonstrate: every one of spec §65's nine
states and §11's six sides is actually storable, expiring a session reaches the
photographs, and the cascade stops at the catalog.

The nine-and-six tests carry more load than they look like they do. Alembic
compares a check constraint's *name* but not its *text*, so the drift guard in
`test_catalog_schema.py` would not notice a migration whose `IN` list had
diverged from `tcg_api.analysis.tables`. Inserting every value is the only thing
that would.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.analysis.tables import analyses, analysis_sessions, images
from tcg_api.catalog.tables import cards, sets
from tcg_domain.analysis import AnalysisStatus, ImageSide, QualityStatus, SessionStatus

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to inspect",
    ),
]


def alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"


def run_sync(work: Callable[[Connection], Any]) -> Any:
    """Run one synchronous callable against a fresh connection."""

    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(work)
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def query(statement: sa.Executable, parameters: Any = None) -> list[Any]:
    def work(connection: Connection) -> list[Any]:
        return list(connection.execute(statement, parameters))

    return run_sync(work)


def write(statements: list[tuple[sa.Executable, Any]]) -> None:
    """Execute `statements` in one transaction, letting failures propagate."""

    def work(connection: Connection) -> None:
        with connection.begin():
            for statement, parameters in statements:
                connection.execute(statement, parameters)

    run_sync(work)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Every test in this module reads the schema at `head`.

    `test_migrations.py` deliberately leaves the database at `base`, and pytest
    makes no promise about which module runs first.
    """
    alembic("upgrade", "head")


@pytest.fixture(autouse=True)
def empty_tables():
    def truncate(connection: Connection) -> None:
        with connection.begin():
            # Child-first, and the catalog too: `analyses.card_id` references
            # `cards`, so a card left behind by another module would survive into
            # these tests and a card inserted here must not survive out of them.
            connection.execute(
                sa.text(
                    "TRUNCATE images, analyses, analysis_sessions, "
                    "card_external_ids, cards, sets RESTART IDENTITY CASCADE"
                )
            )

    run_sync(truncate)
    yield
    run_sync(truncate)


# ---------------------------------------------------------------------------
# Fixture data. Built here rather than loaded from `database/seeds` so that a
# schema test fails for schema reasons and never because a seed changed.
# ---------------------------------------------------------------------------
SET_ID = uuid.UUID("22222222-2222-5222-8222-222222222222")
CARD_ID = uuid.UUID("33333333-3333-5333-8333-333333333333")
SESSION_ID = uuid.UUID("44444444-4444-5444-8444-444444444444")
ANALYSIS_ID = uuid.UUID("55555555-5555-5555-8555-555555555555")

DIGEST = "a" * 64
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def insert_card() -> None:
    """A minimal catalog row, so `analyses.card_id` has something to point at."""
    write(
        [
            (
                sa.insert(sets),
                {
                    "id": SET_ID,
                    "game": "pokemon",
                    "language": "en",
                    "set_code": "BS",
                    "name": "Base Set",
                },
            ),
            (
                sa.insert(cards),
                {
                    "id": CARD_ID,
                    "game": "pokemon",
                    "language": "en",
                    "set_id": SET_ID,
                    "card_number": "4/102",
                    "name": "Charizard",
                    "variant": "unlimited-holo",
                },
            ),
        ]
    )


def session_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": SESSION_ID,
        "anonymous_session_id": "wCq3nB0Xr4h8kJ2vL7pT1yZ6sD9aF5gE",
        "expires_at": NOW + timedelta(days=7),
        "application_version": "0.1.0",
    }
    return values | overrides


def insert_session(**overrides: Any) -> uuid.UUID:
    values = session_values(**overrides)
    write([(sa.insert(analysis_sessions), values)])
    return uuid.UUID(str(values["id"]))


def analysis_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"id": ANALYSIS_ID, "session_id": SESSION_ID}
    return values | overrides


def insert_analysis(**overrides: Any) -> uuid.UUID:
    values = analysis_values(**overrides)
    write([(sa.insert(analyses), values)])
    return uuid.UUID(str(values["id"]))


def image_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "analysis_id": ANALYSIS_ID,
        "side": ImageSide.FRONT.value,
        "original_uri": "analyses/55555555/front.jpg",
        "mime_type": "image/jpeg",
        "sha256": DIGEST,
    }
    return values | overrides


def insert_image(**overrides: Any) -> None:
    write([(sa.insert(images), image_values(**overrides))])


def count(table: sa.Table) -> int:
    return int(query(sa.select(sa.func.count()).select_from(table))[0][0])


# ---------------------------------------------------------------------------
# The vocabularies, against the database the migration built
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", list(AnalysisStatus), ids=[s.value for s in AnalysisStatus])
def test_every_one_of_the_nine_states_is_storable(status: AnalysisStatus) -> None:
    """Spec §65's nine, one insert each.

    The migration writes its `IN` list as a literal and Alembic never compares
    that text, so this is the check that the literal and `tables.py` agree.
    """
    insert_session()

    insert_analysis(status=status.value)

    assert query(sa.select(analyses.c.status)) == [(status.value,)]


def test_a_state_the_specification_does_not_name_is_refused() -> None:
    insert_session()

    with pytest.raises(IntegrityError, match="status_is_a_known_analysis_state"):
        insert_analysis(status="almost_done")


def test_queued_is_not_storable() -> None:
    """§65 answers `queued` from the run endpoint and lists nine states without it."""
    insert_session()

    with pytest.raises(IntegrityError, match="status_is_a_known_analysis_state"):
        insert_analysis(status="queued")


def test_an_analysis_starts_created() -> None:
    insert_session()
    write([(sa.insert(analyses), {"id": ANALYSIS_ID, "session_id": SESSION_ID})])

    assert query(sa.select(analyses.c.status)) == [(AnalysisStatus.CREATED.value,)]


@pytest.mark.parametrize("side", list(ImageSide), ids=[s.value for s in ImageSide])
def test_every_one_of_the_six_sides_is_storable(side: ImageSide) -> None:
    """The issue's acceptance criterion: guided photography needs no migration."""
    insert_session()
    insert_analysis()

    insert_image(side=side.value)

    assert query(sa.select(images.c.side)) == [(side.value,)]


def test_a_side_the_specification_does_not_name_is_refused() -> None:
    insert_session()
    insert_analysis()

    with pytest.raises(IntegrityError, match="side_is_a_known_side"):
        insert_image(side="edge")


@pytest.mark.parametrize("status", list(QualityStatus), ids=[s.value for s in QualityStatus])
def test_every_quality_status_is_storable(status: QualityStatus) -> None:
    insert_session()
    insert_analysis()

    insert_image(quality_status=status.value, quality_score=0.87)

    assert query(sa.select(images.c.quality_status)) == [(status.value,)]


def test_an_image_may_have_no_quality_verdict_yet() -> None:
    """NULL until the quality gate has run, which is a later issue."""
    insert_session()
    insert_analysis()

    insert_image()

    assert query(sa.select(images.c.quality_status, images.c.quality_score)) == [(None, None)]


def test_a_quality_status_the_specification_does_not_name_is_refused() -> None:
    insert_session()
    insert_analysis()

    with pytest.raises(IntegrityError, match="quality_status_is_a_known_status"):
        insert_image(quality_status="excellent")


@pytest.mark.parametrize("status", list(SessionStatus), ids=[s.value for s in SessionStatus])
def test_every_session_state_is_storable(status: SessionStatus) -> None:
    insert_session(status=status.value)

    assert query(sa.select(analysis_sessions.c.status)) == [(status.value,)]


def test_a_session_state_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(IntegrityError, match="status_is_a_known_session_state"):
        insert_session(status="abandoned")


def test_a_session_starts_active() -> None:
    insert_session()

    assert query(sa.select(analysis_sessions.c.status)) == [(SessionStatus.ACTIVE.value,)]


# ---------------------------------------------------------------------------
# The cascade: session → analysis → image, and where it stops
# ---------------------------------------------------------------------------
def test_deleting_a_session_deletes_its_analyses_and_their_images() -> None:
    """One statement is the whole of the retention sweep's database side."""
    insert_session()
    insert_analysis()
    insert_image()

    write([(sa.delete(analysis_sessions), None)])

    assert count(analyses) == 0
    assert count(images) == 0


def test_deleting_an_analysis_deletes_its_images_but_not_its_session() -> None:
    """The middle link on its own.

    Without this, the test above would pass even if `images.analysis_id` were
    `SET NULL` and the images were quietly orphaned rather than removed.
    """
    insert_session()
    insert_analysis()
    insert_image()

    write([(sa.delete(analyses), None)])

    assert count(images) == 0
    assert count(analysis_sessions) == 1


def test_the_cascade_stops_at_the_catalog() -> None:
    """Expiring a user's session must never reach shared reference data."""
    insert_card()
    insert_session()
    insert_analysis(card_id=CARD_ID)
    insert_image()

    write([(sa.delete(analysis_sessions), None)])

    assert count(cards) == 1


def test_a_card_may_not_be_deleted_while_an_analysis_names_it() -> None:
    """RESTRICT, not SET NULL: a stored analysis does not quietly change its mind."""
    insert_card()
    insert_session()
    insert_analysis(card_id=CARD_ID)

    with pytest.raises(IntegrityError, match="fk_analyses_card_id_cards"):
        write([(sa.delete(cards), None)])


def test_an_analysis_may_not_name_a_card_the_catalog_does_not_hold() -> None:
    insert_session()

    with pytest.raises(IntegrityError, match="fk_analyses_card_id_cards"):
        insert_analysis(card_id=uuid.uuid4())


def test_an_analysis_has_no_card_until_the_user_confirms_one() -> None:
    """Spec §20 makes confirmation a step in the pipeline, not a precondition."""
    insert_session()

    insert_analysis()

    assert query(sa.select(analyses.c.card_id)) == [(None,)]


def test_an_analysis_cannot_exist_without_a_session() -> None:
    with pytest.raises(IntegrityError, match="fk_analyses_session_id_analysis_sessions"):
        insert_analysis()


# ---------------------------------------------------------------------------
# Privacy and retention (spec §53, §54)
# ---------------------------------------------------------------------------
def test_a_session_must_say_when_it_expires() -> None:
    """ "Kept forever" is the failure spec §54 describes; it is not representable."""
    with pytest.raises(IntegrityError, match="expires_at"):
        write([(sa.insert(analysis_sessions), session_values(expires_at=None))])


def test_a_session_may_not_expire_before_it_begins() -> None:
    with pytest.raises(IntegrityError, match="expires_after_it_was_created"):
        insert_session(expires_at=datetime(2020, 1, 1, tzinfo=UTC))


def test_two_sessions_cannot_share_a_token() -> None:
    """The token is the only thing separating one user's photographs from another's."""
    insert_session()

    with pytest.raises(IntegrityError, match="uq_analysis_sessions_anonymous_session_id"):
        insert_session(id=uuid.uuid4())


def test_the_expiry_sweep_can_find_what_is_due() -> None:
    """The query the retention job runs, against the index declared for it.

    The lapsed session has to be written with a `created_at` of its own: a row
    cannot be inserted already expired, which is `expires_after_it_was_created`
    doing its job rather than an obstacle.
    """
    insert_session(
        created_at=datetime(2019, 1, 1, tzinfo=UTC),
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    insert_session(
        id=uuid.uuid4(),
        anonymous_session_id="not-the-same-token-at-all-0123456789",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )

    due = query(
        sa.select(sa.func.count())
        .select_from(analysis_sessions)
        .where(analysis_sessions.c.expires_at < sa.func.now())
    )

    assert due == [(1,)]


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def test_an_analysis_holds_one_image_per_side() -> None:
    """A retake replaces the front rather than appending a second one."""
    insert_session()
    insert_analysis()
    insert_image(side=ImageSide.FRONT.value)

    with pytest.raises(IntegrityError, match="uq_images_analysis_id_side"):
        insert_image(side=ImageSide.FRONT.value)


def test_an_analysis_holds_a_front_and_a_back_at_once() -> None:
    insert_session()
    insert_analysis()

    insert_image(side=ImageSide.FRONT.value)
    insert_image(side=ImageSide.BACK.value)

    assert count(images) == 2


def test_two_analyses_may_hold_the_same_bytes() -> None:
    """Two users photographing one card is a coincidence, not a conflict.

    Deduplicating across users is explicitly not a product feature, so the digest
    index is not unique.
    """
    second = uuid.UUID("66666666-6666-5666-8666-666666666666")
    insert_session()
    insert_analysis()
    insert_analysis(id=second)

    insert_image()
    insert_image(analysis_id=second)

    assert count(images) == 2


@pytest.mark.parametrize(
    ("digest", "why"),
    [
        ("A" * 64, "uppercase"),
        ("a" * 63, "too short"),
        ("a" * 65, "too long"),
        (f"sha256:{'a' * 64}", "prefixed"),
        ("g" * 64, "not hexadecimal"),
    ],
    ids=["uppercase", "short", "long", "prefixed", "non-hex"],
)
def test_a_digest_that_is_not_bare_lowercase_hex_is_refused(digest: str, why: str) -> None:
    """One writer emitting a different shape would silently double the cache."""
    insert_session()
    insert_analysis()

    with pytest.raises(IntegrityError, match="sha256_is_lowercase_hex"):
        insert_image(sha256=digest)


@pytest.mark.parametrize("column", ["width", "height"])
def test_a_zero_dimension_is_refused(column: str) -> None:
    """Zero is not a smaller image; it is a division by zero waiting for centering."""
    insert_session()
    insert_analysis()

    dimensions = {"width": 1, "height": 1} | {column: 0}

    with pytest.raises(IntegrityError, match="dimensions_are_positive"):
        insert_image(**dimensions)


def test_an_image_has_no_dimensions_until_it_is_normalized() -> None:
    """Normalization writes `normalized_uri`, `width` and `height` together."""
    insert_session()
    insert_analysis()

    insert_image()

    assert query(sa.select(images.c.normalized_uri, images.c.width, images.c.height)) == [
        (None, None, None)
    ]


def test_an_image_cannot_exist_without_an_analysis() -> None:
    insert_session()

    with pytest.raises(IntegrityError, match="fk_images_analysis_id_analyses"):
        insert_image()


# ---------------------------------------------------------------------------
# `completed_at`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status",
    [AnalysisStatus.COMPLETED, AnalysisStatus.FAILED],
    ids=["completed", "failed"],
)
def test_a_terminal_analysis_may_record_when_it_finished(status: AnalysisStatus) -> None:
    insert_session()

    insert_analysis(status=status.value, completed_at=NOW)

    assert query(sa.select(analyses.c.completed_at)) != [(None,)]


def test_an_unfinished_analysis_may_not_claim_a_completion_time() -> None:
    """Not a smaller version of the truth — a different claim."""
    insert_session()

    with pytest.raises(IntegrityError, match="completed_at_accompanies_a_terminal_status"):
        insert_analysis(status=AnalysisStatus.ANALYZING.value, completed_at=NOW)


def test_a_failed_analysis_need_not_have_a_completion_time() -> None:
    """Whether a reaped job records one is the state machine's question, not this table's."""
    insert_session()

    insert_analysis(status=AnalysisStatus.FAILED.value)

    assert query(sa.select(analyses.c.completed_at)) == [(None,)]


# ---------------------------------------------------------------------------
# What this issue deliberately does not carry
# ---------------------------------------------------------------------------
def test_the_reproducibility_versions_are_recorded_where_the_specification_puts_them() -> None:
    """§12's three version columns exist and start empty; §57's other two do not exist.

    They belong to the reproducibility-record issue, which must resolve a
    published identifier at run time rather than store a pointer to "current".
    """
    insert_session()
    insert_analysis()

    row = query(
        sa.select(
            analyses.c.model_bundle_version,
            analyses.c.market_snapshot_id,
            analyses.c.economic_configuration_id,
        )
    )

    assert row == [(None, None, None)]
    assert "card_database_version" not in analyses.columns
    assert "grading_rules_version" not in analyses.columns


def test_the_schema_still_needs_no_postgresql_extension() -> None:
    """A vocabulary is a CHECK; nothing here reached for a type or an extension."""
    installed = {row[0] for row in query(sa.text("SELECT extname FROM pg_extension"))}

    assert installed <= {"plpgsql"}


def test_no_enum_type_was_created() -> None:
    types = {row[0] for row in query(sa.text("SELECT typname FROM pg_type WHERE typtype = 'e'"))}

    assert types == set()
