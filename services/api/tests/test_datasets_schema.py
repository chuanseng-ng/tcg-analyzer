"""The dataset and provenance schema against a real PostgreSQL — #153.

`test_datasets_tables.py` asserts what was *declared*; this asserts what the
database actually does after the migration has run. The two are not the same
claim, and the gap between them is where a CHECK that exists in `tables.py` and
not in the migration would live — Alembic compares a check's name but never its
text, and no triggers at all.

Most of what is here is ADR 0008's gate, one case per test. That is deliberate
repetition: *"a null, an empty string and an absent field are one answer, and it
is refusal"* is the rule most likely to be softened later by somebody with a
directory of photographs and no licence for them, and a single parametrised
"bad provenance is refused" would let five of the six cases be lost in one edit.

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
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.catalog.tables import cards, sets
from tcg_api.datasets.tables import (
    dataset_members,
    dataset_versions,
    physical_copies,
    training_images,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
]

SET_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
CARD_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")

#: A provenance record every constraint accepts — approved class 1, a photograph
#: of a raw card this project owns and then submitted. Each test below spoils
#: exactly one field, so a failure names the rule that was broken rather than
#: "something was wrong".
LEGAL: dict[str, Any] = {
    "side": "front",
    "original_uri": "training/2026/08/aaaa.jpg",
    "mime_type": "image/jpeg",
    "width": 1512,
    "height": 2112,
    "source": "first_party",
    "source_reference": "PSA 12345678",
    "acquisition_method": "photographed_before_submission",
    "license": "owned outright",
    "commercial_use_allowed": True,
    "derivative_use_allowed": True,
    "redistribution_allowed": False,
    "permission_notes": "ADR 0008 risk R1 — the artwork layer, by reference",
    "acquired_at": datetime(2026, 8, 1, tzinfo=UTC),
}

#: ADR 0008's other three approved classes, as the research document fills §29
#: in for each. Only the fields that differ.
OTHER_APPROVED_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "source": "first_party",
        "acquisition_method": "photographed_owned_slab",
        "source_reference": "BGS 87654321",
        "license": "owned outright",
    },
    {
        "source": "contributed",
        "acquisition_method": "contributed_under_written_grant",
        "source_reference": "grant-2026-004",
        "license": "contributor grant grant-2026-004, 2026-08-14",
    },
    {
        "source": "product_upload",
        "acquisition_method": "uploaded_by_user_with_consent",
        "source_reference": str(uuid.uuid4()),
        "license": "consent text v1",
        # Approved class 4 identifies no physical copy — §32's fallback to
        # `source`, and the reason `physical_copy_id` is nullable at all.
        "physical_copy_id": None,
    },
)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` deliberately leaves the database at `base`.

    pytest makes no promise about which module runs first, so every schema module
    brings the database up itself.
    """
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    """TRUNCATE bypasses row-level triggers, which is the only reason these empty.

    `dataset_versions` and `dataset_members` refuse an UPDATE; nothing refuses a
    DELETE, and TRUNCATE would work either way.
    """
    tables = "dataset_members, dataset_versions, training_images, physical_copies, cards, sets"
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def execute(statement: Any, values: Any = None) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement, values) if values else await connection.execute(
                    statement
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def fetch(statement: Any) -> list[Any]:
    async def scenario() -> list[Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(statement)).all())
        finally:
            await engine.dispose()

    return asyncio.run(scenario())


def seed_catalog() -> None:
    execute(
        sa.insert(sets),
        {
            "id": SET_ID,
            "game": "pokemon",
            "language": "en",
            "set_code": "base1",
            "name": "Base Set",
        },
    )
    execute(
        sa.insert(cards),
        {
            "id": CARD_ID,
            "set_id": SET_ID,
            "game": "pokemon",
            "language": "en",
            "card_number": "4/102",
            "name": "Charizard",
        },
    )


def insert_copy(**overrides: Any) -> uuid.UUID:
    identifier = overrides.pop("id", uuid.uuid4())
    execute(sa.insert(physical_copies), {"id": identifier, **overrides})
    return identifier


def insert_image(**overrides: Any) -> uuid.UUID:
    identifier = uuid.uuid4()
    row = {
        "id": identifier,
        "sha256": f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
        **LEGAL,
        **overrides,
    }
    execute(sa.insert(training_images), row)
    return identifier


def insert_version(**overrides: Any) -> uuid.UUID:
    identifier = uuid.uuid4()
    execute(
        sa.insert(dataset_versions),
        {
            "id": identifier,
            "version": "pokemon-condition-v0.1.0",
            "split_seed": 20260828,
            **overrides,
        },
    )
    return identifier


# ---------------------------------------------------------------------------
# ADR 0008's gate — one test per way of not saying
# ---------------------------------------------------------------------------


def test_an_unstated_commercial_use_right_is_refused() -> None:
    """Spec §29 by name: reject an image whose commercial-use status is unknown.

    NULL, not false — this is the case the obvious spelling of the constraint
    would let through, because `NULL AND true` is `NULL` and a CHECK passes on
    `NULL`.
    """
    with pytest.raises(IntegrityError, match="provenance_permits_training"):
        insert_image(commercial_use_allowed=None)


def test_a_refused_commercial_use_right_is_refused() -> None:
    with pytest.raises(IntegrityError, match="provenance_permits_training"):
        insert_image(commercial_use_allowed=False)


def test_an_unstated_derivative_use_right_is_refused() -> None:
    """A trained model is a derivative work; §28's pipeline ends in Training."""
    with pytest.raises(IntegrityError, match="provenance_permits_training"):
        insert_image(derivative_use_allowed=None)


def test_a_refused_derivative_use_right_is_refused() -> None:
    with pytest.raises(IntegrityError, match="provenance_permits_training"):
        insert_image(derivative_use_allowed=False)


def test_an_absent_licence_is_refused() -> None:
    with pytest.raises(IntegrityError, match="provenance_permits_training"):
        insert_image(license=None)


def test_a_blank_licence_is_refused() -> None:
    """ADR 0008's third answer: an empty string is not a licence."""
    with pytest.raises(IntegrityError, match="provenance_permits_training"):
        insert_image(license="   ")


def test_an_unstated_redistribution_right_is_refused() -> None:
    """Recorded rather than gated, so NOT NULL is the only thing guarding it.

    The refusal names the column rather than the gate, and that is correct: ADR
    0008 makes this false everywhere, so an absent answer is a record nobody
    completed rather than a right nobody has.
    """
    with pytest.raises(IntegrityError, match="redistribution_allowed"):
        insert_image(redistribution_allowed=None)


@pytest.mark.parametrize(
    "overrides", OTHER_APPROVED_SOURCES, ids=lambda o: str(o["acquisition_method"])
)
def test_every_approved_source_is_accepted(overrides: dict[str, Any]) -> None:
    """The gate refuses; it must not also obstruct. All four of ADR 0008's classes."""
    insert_image(**overrides)

    assert len(fetch(sa.select(training_images.c.id))) == 1


def test_an_unapproved_source_is_accepted_by_the_database() -> None:
    """Deliberately the inverse assertion — `grading_rules.company`'s precedent.

    A fifth approved source should cost an ADR and no migration, so the schema
    refuses the *rights* and the ingestion path refuses the *list*.
    """
    insert_image(source="scraped_from_a_marketplace")

    assert len(fetch(sa.select(training_images.c.id))) == 1


# ---------------------------------------------------------------------------
# Spec §32's grouping keys — #150
# ---------------------------------------------------------------------------


def test_two_copies_of_one_card_are_two_rows() -> None:
    """§32 requires two different copies of one printing to be splittable apart."""
    seed_catalog()
    first, second = insert_copy(), insert_copy()
    insert_image(physical_copy_id=first, card_id=CARD_ID)
    insert_image(physical_copy_id=second, card_id=CARD_ID)

    assert first != second
    assert len({row.physical_copy_id for row in fetch(sa.select(training_images))}) == 2


def test_one_copy_photographed_twice_keeps_one_identifier() -> None:
    """Two hashes, one copy — precisely the leakage §32 names."""
    copy = insert_copy()
    insert_image(physical_copy_id=copy, side="front")
    insert_image(physical_copy_id=copy, side="back")

    assert {row.physical_copy_id for row in fetch(sa.select(training_images))} == {copy}


def test_a_duplicate_certification_number_is_refused() -> None:
    """One slab is one copy; entered twice it would split across train and test."""
    insert_copy(certification_company="psa", certification_number="12345678")

    with pytest.raises(IntegrityError, match="uq_physical_copies_certification"):
        insert_copy(certification_company="psa", certification_number="12345678")


def test_the_same_number_at_two_companies_is_two_copies() -> None:
    """Certification numbers are per-company, so the pair is what identifies a slab."""
    insert_copy(certification_company="psa", certification_number="12345678")
    insert_copy(certification_company="bgs", certification_number="12345678")

    assert len(fetch(sa.select(physical_copies.c.id))) == 2


def test_two_raw_copies_are_not_collapsed_into_one() -> None:
    """NULLS NOT DISTINCT is deliberately not set: every unidentified copy is its own row."""
    insert_copy()
    insert_copy()

    assert len(fetch(sa.select(physical_copies.c.id))) == 2


def test_half_a_certification_is_refused() -> None:
    with pytest.raises(IntegrityError, match="certification_is_a_company_and_a_number"):
        insert_copy(certification_company="psa")


def test_an_unsupported_certification_company_is_refused() -> None:
    with pytest.raises(IntegrityError, match="certification_company_is_supported"):
        insert_copy(certification_company="cgc", certification_number="1")


def test_a_copy_the_corpus_names_cannot_be_deleted() -> None:
    """RESTRICT: a copy something was photographed against stays resolvable."""
    copy = insert_copy()
    insert_image(physical_copy_id=copy)

    with pytest.raises(IntegrityError, match="fk_training_images_physical_copy_id"):
        execute(sa.delete(physical_copies).where(physical_copies.c.id == copy))


def test_the_same_photograph_cannot_be_ingested_twice() -> None:
    """The exact-duplicate half of ADR 0009's deduplication, for free."""
    digest = f"{uuid.uuid4().hex}{uuid.uuid4().hex}"
    insert_image(sha256=digest)

    with pytest.raises(IntegrityError, match="uq_training_images_sha256"):
        insert_image(sha256=digest)


def test_an_uppercase_digest_is_refused() -> None:
    """One spelling of a hash, or the unique constraint stops meaning anything."""
    with pytest.raises(IntegrityError, match="sha256_is_lowercase_hex"):
        insert_image(sha256="A" * 64)


def test_a_zero_dimension_is_refused() -> None:
    with pytest.raises(IntegrityError, match="dimensions_are_positive"):
        insert_image(width=0)


def test_a_blank_source_is_refused() -> None:
    """`source` is §32's fallback grouping key, so a blank one is a group of everything."""
    with pytest.raises(IntegrityError, match="source_is_not_blank"):
        insert_image(source="  ")


def test_a_training_image_can_be_corrected() -> None:
    """Deliberately the inverse assertion — this table carries no trigger.

    A card is identified after ingestion and ADR 0009 anticipates correcting
    provenance by script, so a write-once row here would be wrong. Adding a
    trigger later fails this test rather than quietly breaking both.
    """
    seed_catalog()
    image = insert_image()

    execute(sa.update(training_images).where(training_images.c.id == image).values(card_id=CARD_ID))

    assert fetch(sa.select(training_images.c.card_id))[0].card_id == CARD_ID


def test_a_certification_number_can_arrive_after_the_photographs() -> None:
    """Approved class 1 photographs a raw card and submits it — #150's whole point."""
    copy = insert_copy()

    execute(
        sa.update(physical_copies)
        .where(physical_copies.c.id == copy)
        .values(certification_company="psa", certification_number="99999999")
    )

    assert fetch(sa.select(physical_copies.c.certification_number))[0][0] == "99999999"


# ---------------------------------------------------------------------------
# Spec §31's versions
# ---------------------------------------------------------------------------


def test_a_version_may_not_be_a_pointer() -> None:
    """§31: a model must never simply reference `/latest/`."""
    with pytest.raises(IntegrityError, match="version_is_an_explicit_identifier"):
        insert_version(version="latest")


def test_a_published_version_cannot_be_updated() -> None:
    """§31's immutability, and the refusal names the table via TG_TABLE_NAME."""
    version = insert_version()

    with pytest.raises(IntegrityError, match="dataset_versions is frozen"):
        execute(
            sa.update(dataset_versions).where(dataset_versions.c.id == version).values(split_seed=1)
        )


def test_a_members_split_cannot_be_moved() -> None:
    """One function serves both tables, so TG_TABLE_NAME is what tells them apart."""
    version, image = insert_version(), insert_image()
    execute(
        sa.insert(dataset_members),
        {"dataset_version_id": version, "training_image_id": image, "split": "train"},
    )

    with pytest.raises(IntegrityError, match="dataset_members is frozen"):
        execute(sa.update(dataset_members).values(split="test"))


def test_a_version_can_be_deleted() -> None:
    """UPDATE only, deliberately not DELETE — as every other domain in this schema."""
    version = insert_version()

    execute(sa.delete(dataset_versions).where(dataset_versions.c.id == version))

    assert fetch(sa.select(dataset_versions.c.id)) == []


def test_deleting_a_version_takes_its_members_with_it() -> None:
    """CASCADE: a membership row means nothing without its version."""
    version, image = insert_version(), insert_image()
    execute(
        sa.insert(dataset_members),
        {"dataset_version_id": version, "training_image_id": image, "split": "train"},
    )

    execute(sa.delete(dataset_versions).where(dataset_versions.c.id == version))

    assert fetch(sa.select(dataset_members)) == []


def test_an_image_a_frozen_version_names_cannot_be_deleted() -> None:
    """ADR 0008 grants retention after a contributor withdraws, and this is why.

    §31 means a version cannot un-include an image, so deleting one would leave
    a manifest naming bytes nobody can produce.
    """
    version, image = insert_version(), insert_image()
    execute(
        sa.insert(dataset_members),
        {"dataset_version_id": version, "training_image_id": image, "split": "validation"},
    )

    with pytest.raises(IntegrityError, match="fk_dataset_members_training_image_id"):
        execute(sa.delete(training_images).where(training_images.c.id == image))


def test_an_unknown_split_is_refused() -> None:
    version, image = insert_version(), insert_image()

    with pytest.raises(IntegrityError, match="split_is_a_known_split"):
        execute(
            sa.insert(dataset_members),
            {"dataset_version_id": version, "training_image_id": image, "split": "holdout"},
        )


def test_one_image_appears_in_a_version_once() -> None:
    """The natural key: an image in two splits of one version is the leakage itself."""
    version, image = insert_version(), insert_image()
    execute(
        sa.insert(dataset_members),
        {"dataset_version_id": version, "training_image_id": image, "split": "train"},
    )

    with pytest.raises(IntegrityError, match="pk_dataset_members"):
        execute(
            sa.insert(dataset_members),
            {"dataset_version_id": version, "training_image_id": image, "split": "test"},
        )


def test_the_ordinal_is_assigned_by_the_database() -> None:
    """GENERATED ALWAYS, so no writer can place a version out of sequence."""
    insert_version(version="pokemon-condition-v0.1.0")
    insert_version(version="pokemon-condition-v0.2.0")

    rows = fetch(sa.select(dataset_versions.c.ordinal).order_by(dataset_versions.c.ordinal))

    assert [row.ordinal for row in rows] == [1, 2]
