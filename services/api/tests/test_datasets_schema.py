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
    centering_measurements,
    dataset_members,
    dataset_versions,
    image_annotations,
    physical_copies,
    training_image_fingerprints,
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
    tables = (
        "image_annotations, centering_measurements, training_image_fingerprints, "
        "dataset_members, dataset_versions, training_images, physical_copies, cards, sets"
    )
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


# ---------------------------------------------------------------------------
# #155's fingerprints, against the real constraints
# ---------------------------------------------------------------------------

#: A hash the grammar accepts: 16 lowercase hex characters, 64 bits.
A_HASH = "0f1e2d3c4b5a6978"


def insert_fingerprint(image_id: uuid.UUID, **overrides: Any) -> None:
    execute(
        sa.insert(training_image_fingerprints),
        {
            "training_image_id": image_id,
            "perceptual_hash": A_HASH,
            "perceptual_hash_rotated": A_HASH,
            "hash_version": "dhash-8x8-v0.1.0+detector+normalizer",
            **overrides,
        },
    )


def test_a_fingerprint_is_stored_for_an_image() -> None:
    image_id = insert_image()

    insert_fingerprint(image_id)

    rows = fetch(sa.select(training_image_fingerprints))
    assert len(rows) == 1
    assert rows[0].perceptual_hash == A_HASH


def test_an_uppercase_hash_is_refused() -> None:
    """The grammar is the same one `sha256` uses, and for the same reason.

    Two spellings of one hash compare as different bits, so the column admits one.
    """
    image_id = insert_image()

    with pytest.raises(IntegrityError, match="hashes_are_lowercase_hex"):
        insert_fingerprint(image_id, perceptual_hash=A_HASH.upper())


def test_a_hash_of_the_wrong_width_is_refused() -> None:
    image_id = insert_image()

    with pytest.raises(IntegrityError, match="hashes_are_lowercase_hex"):
        insert_fingerprint(image_id, perceptual_hash="0f1e2d3c")


def test_half_a_fingerprint_is_refused() -> None:
    """`physical_copies`' certification rule, one table along.

    Half a fingerprint is not a smaller fingerprint; it is a row whose distance
    to anything is undefined, because the rotated hash is one of three terms the
    comparison takes.
    """
    image_id = insert_image()

    with pytest.raises(IntegrityError, match="both_hashes_or_neither"):
        insert_fingerprint(image_id, perceptual_hash_rotated=None)


def test_an_image_with_no_card_located_records_a_row_with_no_hashes() -> None:
    """Neither hash, and that is an answer rather than a gap.

    It is what stops the next pass decoding those same bytes again — and a
    version bump is what makes it try once more.
    """
    image_id = insert_image()

    insert_fingerprint(image_id, perceptual_hash=None, perceptual_hash_rotated=None)

    assert fetch(sa.select(training_image_fingerprints))[0].perceptual_hash is None


def test_a_fingerprint_can_be_recomputed() -> None:
    """The inverse assertion: this table carries no immutability trigger.

    A detector or normalizer bump rewrites every row, so a trigger added here
    later fails this test rather than being discovered by a pass that cannot
    finish.
    """
    image_id = insert_image()
    insert_fingerprint(image_id)

    execute(
        sa.update(training_image_fingerprints).values(
            perceptual_hash="ffffffffffffffff",
            perceptual_hash_rotated="ffffffffffffffff",
            hash_version="dhash-8x8-v0.2.0+detector+normalizer",
        )
    )

    assert fetch(sa.select(training_image_fingerprints))[0].perceptual_hash == "f" * 16


def test_one_image_carries_at_most_one_fingerprint() -> None:
    image_id = insert_image()
    insert_fingerprint(image_id)

    with pytest.raises(IntegrityError, match="pk_training_image_fingerprints"):
        insert_fingerprint(image_id)


def test_a_fingerprint_goes_when_its_image_does() -> None:
    """CASCADE, where `dataset_members` restricts.

    Spec §54's disposal and a withdrawn contributor both need an image removable,
    and derived data must never be the thing standing in the way.
    """
    image_id = insert_image()
    insert_fingerprint(image_id)

    execute(sa.delete(training_images).where(training_images.c.id == image_id))

    assert fetch(sa.select(training_image_fingerprints)) == []


def test_a_fingerprint_needs_an_image_that_exists() -> None:
    with pytest.raises(IntegrityError, match="fk_training_image_fingerprints_image"):
        insert_fingerprint(uuid.uuid4())


# ---------------------------------------------------------------------------
# #158's annotations, against the database rather than the declaration
# ---------------------------------------------------------------------------
#: A corner annotation every constraint accepts. Each test below spoils exactly
#: one field, so a failure names the rule that was broken.
LEGAL_ANNOTATION: dict[str, Any] = {
    "kind": "corner",
    "region": "top_left",
    "label": "whitening",
    "severity": "minor",
    "confidence": 0.8,
    "representation": "normalized",
    "annotator_id": "annotator-1",
}


def insert_annotation(**overrides: Any) -> uuid.UUID:
    identifier = overrides.pop("id", uuid.uuid4())
    image = overrides.pop("training_image_id", None) or insert_image()
    execute(
        sa.insert(image_annotations),
        {"id": identifier, "training_image_id": image, **LEGAL_ANNOTATION, **overrides},
    )
    return identifier


def insert_centering(**overrides: Any) -> uuid.UUID:
    identifier = overrides.pop("id", uuid.uuid4())
    image = overrides.pop("training_image_id", None) or insert_image()
    execute(
        sa.insert(centering_measurements),
        {
            "id": identifier,
            "training_image_id": image,
            "horizontal": 0.52,
            "vertical": 0.48,
            "confidence": 0.9,
            "annotator_id": "annotator-1",
            **overrides,
        },
    )
    return identifier


def test_an_annotation_carrying_uncertainty_round_trips() -> None:
    """§30's eleven, stored and read back — the acceptance criterion.

    The confidence in particular: it is one of the eleven, it comes back a real
    number, and `unknown` beside it is the other half of the same rule.
    """
    seed_catalog()
    image = insert_image(card_id=CARD_ID)
    insert_annotation(
        training_image_id=image,
        kind="surface",
        region=None,
        label="unknown",
        severity=None,
        confidence=0.25,
        bbox_x=0.1,
        bbox_y=0.2,
        bbox_width=0.05,
        bbox_height=0.05,
        polygon=[[0.1, 0.2], [0.15, 0.2], [0.15, 0.25]],
    )

    (row,) = fetch(sa.select(image_annotations))

    assert row.label == "unknown"
    assert row.severity is None
    assert row.confidence == pytest.approx(0.25)
    assert row.polygon == [[0.1, 0.2], [0.15, 0.2], [0.15, 0.25]]
    assert row.metadata == {}
    assert row.created_at is not None


def test_a_defect_without_a_severity_is_refused() -> None:
    """§17 requires one beside every defect, and `chipping` is a defect."""
    with pytest.raises(IntegrityError, match="a_defect_carries_a_severity"):
        insert_annotation(label="chipping", severity=None)


def test_a_clean_corner_carrying_a_severity_is_refused() -> None:
    """The other direction of the same equality — there is nothing to rate."""
    with pytest.raises(IntegrityError, match="a_defect_carries_a_severity"):
        insert_annotation(label="clean", severity="minor")


def test_a_clean_corner_without_a_severity_is_accepted() -> None:
    """§14 opens with `clean`: a corner inspected and found sound is recorded."""
    insert_annotation(label="clean", severity=None)

    (row,) = fetch(sa.select(image_annotations))

    assert (row.label, row.severity) == ("clean", None)


def test_a_severity_outside_the_three_is_refused() -> None:
    with pytest.raises(IntegrityError, match="severity_is_a_known_severity"):
        insert_annotation(severity="catastrophic")


def test_a_surface_label_on_a_corner_is_refused() -> None:
    """The three vocabularies are three lists, and this is why that matters."""
    with pytest.raises(IntegrityError, match="kind_region_and_label_agree"):
        insert_annotation(label="scratch")


def test_an_edge_label_on_a_corner_is_refused() -> None:
    """`rough_cut` is a cutting defect an edge has and a corner has not."""
    with pytest.raises(IntegrityError, match="kind_region_and_label_agree"):
        insert_annotation(label="rough_cut")


def test_a_surface_annotation_naming_a_region_is_refused() -> None:
    """§16 names no positions; a surface defect is placed by its box."""
    with pytest.raises(IntegrityError, match="kind_region_and_label_agree"):
        insert_annotation(kind="surface", region="top_left", label="scratch", severity="minor")


def test_a_corner_annotation_without_a_region_is_refused() -> None:
    with pytest.raises(IntegrityError, match="kind_region_and_label_agree"):
        insert_annotation(region=None)


def test_an_edge_region_on_a_corner_is_refused() -> None:
    """`top` is an edge and `top_left` is a corner; the two sets do not overlap."""
    with pytest.raises(IntegrityError, match="kind_region_and_label_agree"):
        insert_annotation(region="top")


def test_a_surface_annotation_may_name_the_original_photograph() -> None:
    """#175, and ADR 0010's one route back to a fine-class signal.

    A `scratch` with a box in the original photograph's frame is exactly the row
    the artifact could not honestly hold, and it round-trips.
    """
    insert_annotation(
        kind="surface",
        region=None,
        label="scratch",
        severity="minor",
        representation="original",
        bbox_x=0.4,
        bbox_y=0.5,
        bbox_width=0.01,
        bbox_height=0.02,
    )

    (row,) = fetch(sa.select(image_annotations))

    assert row.representation == "original"


def test_a_corner_annotation_naming_the_original_is_refused() -> None:
    """ADR 0010: #175 changes the coordinate space of *surface* annotations only."""
    with pytest.raises(IntegrityError, match="only_a_surface_marks_the_original"):
        insert_annotation(representation="original")


def test_a_representation_outside_the_two_frames_is_refused() -> None:
    # On a surface row, so the membership CHECK is the one that fires —
    # 'photograph' on a corner would trip the only-a-surface rule first.
    with pytest.raises(IntegrityError, match="representation_is_a_known_representation"):
        insert_annotation(
            kind="surface", region=None, label="scratch", representation="photograph"
        )


def test_a_representation_nobody_named_is_refused() -> None:
    """No server default, so silence is a refusal rather than 'normalized'."""
    with pytest.raises(IntegrityError, match="representation"):
        insert_annotation(representation=None)


def test_a_confidence_outside_the_unit_interval_is_refused() -> None:
    with pytest.raises(IntegrityError, match="confidence_is_a_unit_interval"):
        insert_annotation(confidence=1.5)


def test_a_bounding_box_running_off_the_artifact_is_refused() -> None:
    """Coordinates are fractions of the 756x1056 artifact, so they fit in [0, 1]."""
    with pytest.raises(IntegrityError, match="bounding_box_lies_inside_the_artifact"):
        insert_annotation(bbox_x=0.9, bbox_y=0.1, bbox_width=0.2, bbox_height=0.1)


def test_a_bounding_box_with_no_area_is_refused() -> None:
    with pytest.raises(IntegrityError, match="bounding_box_lies_inside_the_artifact"):
        insert_annotation(bbox_x=0.1, bbox_y=0.1, bbox_width=0.0, bbox_height=0.1)


def test_three_of_four_box_coordinates_are_refused() -> None:
    """Three of four is not a smaller box, it is a box nobody can draw."""
    with pytest.raises(IntegrityError, match="bounding_box_is_whole_or_absent"):
        insert_annotation(bbox_x=0.1, bbox_y=0.1, bbox_width=0.1)


def test_a_polygon_that_is_not_an_array_is_refused() -> None:
    with pytest.raises(IntegrityError, match="polygon_is_an_array"):
        insert_annotation(polygon={"points": []})


def test_an_annotator_who_could_be_a_person_is_refused() -> None:
    """Spec §53's restraint, enforced rather than requested."""
    with pytest.raises(IntegrityError, match="annotator_id_is_opaque"):
        insert_annotation(annotator_id="someone@example.com")


def test_an_annotation_cannot_be_edited() -> None:
    """A corrected annotation is a new row — #27's and #50's rule, here.

    A dataset version that referenced the old reading must keep meaning what it
    meant, which is the whole reason this domain freezes anything at all.
    """
    identifier = insert_annotation()

    with pytest.raises(Exception, match="image_annotations is append-only"):
        execute(
            sa.update(image_annotations)
            .where(image_annotations.c.id == identifier)
            .values(severity="severe")
        )


def test_a_centering_measurement_cannot_be_edited() -> None:
    identifier = insert_centering()

    with pytest.raises(Exception, match="centering_measurements is append-only"):
        execute(
            sa.update(centering_measurements)
            .where(centering_measurements.c.id == identifier)
            .values(horizontal=0.5)
        )


def test_a_dataset_version_still_refuses_an_update() -> None:
    """#158 adds a second immutability function; this proves it added rather than replaced.

    The two hints differ deliberately, and a downgrade that dropped the wrong
    function would leave this passing silently.
    """
    identifier = insert_version()

    with pytest.raises(Exception, match="dataset_versions is frozen"):
        execute(
            sa.update(dataset_versions)
            .where(dataset_versions.c.id == identifier)
            .values(split_seed=1)
        )


def test_an_annotation_is_removable_and_goes_with_its_image() -> None:
    """CASCADE: ADR 0008's withdrawal must not be blocked by a label."""
    image = insert_image()
    insert_annotation(training_image_id=image)
    insert_centering(training_image_id=image)

    execute(sa.delete(training_images).where(training_images.c.id == image))

    assert fetch(sa.select(image_annotations)) == []
    assert fetch(sa.select(centering_measurements)) == []


def test_centering_measurements_are_stored_and_queried_as_numbers() -> None:
    """§13: ratios rather than qualitative labels, and the issue's own test.

    The `WHERE` is the point — a label could be read back, but only a number can
    be compared in a range the annotator never wrote down.
    """
    insert_centering(horizontal=0.52, vertical=0.48)

    rows = fetch(
        sa.select(centering_measurements).where(
            centering_measurements.c.horizontal.between(0.45, 0.55)
        )
    )

    (row,) = rows
    assert isinstance(row.horizontal, float)
    assert row.vertical == pytest.approx(0.48)


def test_a_borderless_card_measures_one_axis_and_says_so() -> None:
    """§21 names full-art and borderless layouts; one axis has no border to read."""
    insert_centering(horizontal=None, vertical=0.5, confidence=0.4, notes="borderless full art")

    (row,) = fetch(sa.select(centering_measurements))

    assert row.horizontal is None
    assert row.vertical == pytest.approx(0.5)


def test_a_measurement_of_neither_axis_is_refused() -> None:
    """That row records an annotator and a time and nothing else."""
    with pytest.raises(IntegrityError, match="a_measurement_measures_something"):
        insert_centering(horizontal=None, vertical=None)


def test_a_ratio_outside_the_unit_interval_is_refused() -> None:
    """A border cannot be more than all of the border."""
    with pytest.raises(IntegrityError, match="ratios_are_unit_intervals"):
        insert_centering(horizontal=1.4)


def test_a_centering_annotator_who_could_be_a_person_is_refused() -> None:
    with pytest.raises(IntegrityError, match="annotator_id_is_opaque"):
        insert_centering(annotator_id="Ada Lovelace")


def test_an_image_may_carry_a_corrected_annotation_beside_the_original() -> None:
    """Append-only means two rows, and the newest one is the current reading.

    Nothing is unique per image and per region, deliberately: that is what makes
    a correction representable at all, and a surface has as many defects as it
    has.
    """
    image = insert_image()
    insert_annotation(training_image_id=image, severity="minor")
    insert_annotation(training_image_id=image, severity="severe")

    rows = fetch(
        sa.select(image_annotations)
        .where(image_annotations.c.training_image_id == image)
        .order_by(image_annotations.c.created_at)
    )

    assert [row.severity for row in rows] == ["minor", "severe"]
