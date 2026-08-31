"""Spec §31's `dataset_version` and its generated manifest — #157.

Two claims carry this file. A version is frozen: it cannot be updated, and no
code path adds a member to one that already exists. And the manifest is a render
rather than a record: regenerating it from the same rows produces the same bytes,
which is what makes a training run reproducible years after the run.

Nothing here imports the CV stack — `test_import_purity.py` asserts that
separately.

The database half is skipped unless `TCG_API_DATABASE_URL` points at a live
PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets import versioning
from tcg_api.datasets.splitting import CorpusImage, assign_splits, read_corpus, split_corpus
from tcg_api.datasets.tables import (
    dataset_members,
    centering_measurements,
    dataset_versions,
    image_annotations,
    physical_copies,
    training_images,
)
from tcg_api.datasets.versioning import (
    DatasetVersion,
    DatasetVersionRefused,
    Manifest,
    ManifestMember,
    MemberAnnotation,
    MemberCentering,
    create_version,
    manifest_path,
    read_manifest,
    render_manifest,
    write_manifest,
)
from tcg_domain.dataset import DatasetSplit

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to read from",
)

SEED = 20260828
VERSION = "pokemon-condition-v0.1.0"


def _member(
    identifier: str,
    split: DatasetSplit = DatasetSplit.TRAIN,
    *,
    annotations: tuple[MemberAnnotation, ...] = (),
    centering: tuple[MemberCentering, ...] = (),
) -> ManifestMember:
    return ManifestMember(
        training_image_id=uuid.UUID(identifier),
        sha256=identifier.replace("-", "") * 2,
        split=split,
        side="front",
        source="first_party",
        acquisition_method="photographed_owned_slab",
        original_uri=f"training/{identifier}.png",
        annotations=annotations,
        centering=centering,
    )


def _annotation(
    identifier: str,
    *,
    kind: str = "corner",
    region: str | None = "top_left",
    label: str = "whitening",
    severity: str | None = "minor",
    bbox: tuple[float, float, float, float] | None = None,
    representation: str = "normalized",
    minute: int = 0,
) -> MemberAnnotation:
    return MemberAnnotation(
        id=uuid.UUID(identifier),
        kind=kind,
        region=region,
        label=label,
        severity=severity,
        confidence=0.9,
        bbox=bbox,
        representation=representation,
        created_at=datetime(2026, 8, 30, 9, minute, tzinfo=UTC),
    )


def _measurement(identifier: str, *, minute: int = 0) -> MemberCentering:
    return MemberCentering(
        id=uuid.UUID(identifier),
        horizontal=0.55,
        vertical=None,
        confidence=0.9,
        created_at=datetime(2026, 8, 30, 9, minute, tzinfo=UTC),
    )


def _manifest(*members: ManifestMember) -> Manifest:
    return Manifest(
        version=DatasetVersion(
            id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            version=VERSION,
            ordinal=1,
            split_seed=SEED,
            created_at=datetime(2026, 8, 29, 10, 0, tzinfo=UTC),
        ),
        members=members,
    )


# ---------------------------------------------------------------------------
# The manifest is byte-identical for identical rows
# ---------------------------------------------------------------------------
def test_rendering_the_same_manifest_twice_produces_the_same_text() -> None:
    manifest = _manifest(_member("00000000-0000-0000-0000-00000000000a"))

    assert render_manifest(manifest) == render_manifest(manifest)


def test_members_are_ordered_by_identifier_however_they_arrived() -> None:
    """A total order, so a query plan change cannot reorder a published manifest."""
    first = _member("00000000-0000-0000-0000-00000000000a")
    second = _member("00000000-0000-0000-0000-00000000000b", DatasetSplit.TEST)

    assert render_manifest(_manifest(first, second)) == render_manifest(_manifest(second, first))

    listed = json.loads(render_manifest(_manifest(second, first)))["members"]
    assert [entry["training_image_id"] for entry in listed] == [
        str(first.training_image_id),
        str(second.training_image_id),
    ]


def test_the_file_ends_in_one_newline_and_carries_no_carriage_return() -> None:
    """`write_text` translates to `os.linesep` unless told not to.

    A manifest written on Windows would otherwise differ byte for byte from the
    same version regenerated on Linux, which is the one thing this file's
    acceptance criterion forbids.
    """
    text = render_manifest(_manifest(_member("00000000-0000-0000-0000-00000000000a")))

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\r" not in text


def test_written_bytes_use_line_feeds_on_every_platform(tmp_path: Path) -> None:
    manifest = _manifest(_member("00000000-0000-0000-0000-00000000000a"))

    path = write_manifest(manifest, tmp_path)

    assert path == manifest_path(VERSION, tmp_path)
    assert b"\r" not in path.read_bytes()
    assert path.read_bytes().decode("utf-8") == render_manifest(manifest)


def test_nothing_is_stamped_at_render_time() -> None:
    """No generated-at and no application version.

    Either would make the first regeneration differ from the file it replaced,
    and neither is a fact about the corpus. `created_at` is the version row's and
    is therefore stable.
    """
    payload = json.loads(
        render_manifest(_manifest(_member("00000000-0000-0000-0000-0000000000aa")))
    )

    assert payload["created_at"] == "2026-08-29T10:00:00+00:00"
    assert not {
        key for key in payload if "generated" in key or "application" in key or "rendered" in key
    }


def test_no_image_bytes_reach_the_manifest() -> None:
    """ADR 0008 makes `redistribution_allowed` false everywhere.

    Identifiers and content hashes carry no artwork and may be committed; the
    bytes never can, and are not a column to begin with.
    """
    member = _member("00000000-0000-0000-0000-0000000000aa")
    entry = json.loads(render_manifest(_manifest(member)))["members"][0]

    assert set(entry) == {
        "training_image_id",
        "sha256",
        "split",
        "side",
        "source",
        "acquisition_method",
        "original_uri",
        "annotations",
        "centering",
    }


# ---------------------------------------------------------------------------
# Annotations ride on the member — #157's pre-authorized shape, landed by #188
# ---------------------------------------------------------------------------
def test_an_unannotated_member_renders_empty_annotation_lists() -> None:
    """Empty, never absent: a reader must not confuse "no rows" with an old file."""
    entry = json.loads(render_manifest(_manifest(_member("00000000-0000-0000-0000-0000000000aa"))))[
        "members"
    ][0]

    assert entry["annotations"] == []
    assert entry["centering"] == []


def test_a_rendered_annotation_carries_no_annotator_and_no_notes() -> None:
    """§53's restraint over personal data covers the committed file too."""
    member = _member(
        "00000000-0000-0000-0000-0000000000aa",
        annotations=(_annotation("00000000-0000-0000-0000-00000000a001"),),
        centering=(_measurement("00000000-0000-0000-0000-00000000c001"),),
    )

    entry = json.loads(render_manifest(_manifest(member)))["members"][0]

    marker = entry["annotations"][0]
    assert set(marker) == {
        "id",
        "kind",
        "region",
        "label",
        "severity",
        "confidence",
        "representation",
        "created_at",
    }
    measurement = entry["centering"][0]
    assert set(measurement) == {"id", "horizontal", "confidence", "created_at"}


def test_annotation_optionals_are_absent_keys_rather_than_nulls() -> None:
    """The `as_record()` convention: a bbox is four keys or no key at all."""
    member = _member(
        "00000000-0000-0000-0000-0000000000aa",
        annotations=(
            _annotation(
                "00000000-0000-0000-0000-00000000a001",
                kind="surface",
                region=None,
                label="stain",
                bbox=(0.1, 0.2, 0.05, 0.05),
            ),
        ),
    )

    marker = json.loads(render_manifest(_manifest(member)))["members"][0]["annotations"][0]

    assert "region" not in marker
    assert marker["bbox"] == {"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.05}


def test_annotations_render_in_row_order_however_they_arrived() -> None:
    """Ordered by (created_at, id), the append-only tables' own total order."""
    first = _annotation("00000000-0000-0000-0000-00000000a001", minute=1)
    second = _annotation("00000000-0000-0000-0000-00000000a002", minute=2)
    one_way = _member("00000000-0000-0000-0000-0000000000aa", annotations=(first, second))
    other_way = _member("00000000-0000-0000-0000-0000000000aa", annotations=(second, first))

    assert render_manifest(_manifest(one_way)) == render_manifest(_manifest(other_way))

    listed = json.loads(render_manifest(_manifest(one_way)))["members"][0]["annotations"]
    assert [marker["id"] for marker in listed] == [str(first.id), str(second.id)]


def test_a_measured_axis_renders_and_an_unmeasured_one_stays_absent() -> None:
    member = _member(
        "00000000-0000-0000-0000-0000000000aa",
        centering=(_measurement("00000000-0000-0000-0000-00000000c001"),),
    )

    measurement = json.loads(render_manifest(_manifest(member)))["members"][0]["centering"][0]

    assert measurement["horizontal"] == 0.55
    assert "vertical" not in measurement


# ---------------------------------------------------------------------------
# What the manifest derives rather than stores
# ---------------------------------------------------------------------------
def test_counts_proportions_and_provenance_are_derived_from_the_members() -> None:
    contributed = ManifestMember(
        training_image_id=uuid.UUID("00000000-0000-0000-0000-0000000000ff"),
        sha256="f" * 64,
        split=DatasetSplit.TEST,
        side="back",
        source="contributed",
        acquisition_method="contributed_under_written_grant",
        original_uri="training/ff.png",
    )
    manifest = _manifest(
        _member("00000000-0000-0000-0000-00000000000a"),
        _member("00000000-0000-0000-0000-00000000000b"),
        _member("00000000-0000-0000-0000-00000000000c"),
        contributed,
    )

    assert manifest.counts == {
        DatasetSplit.TRAIN: 3,
        DatasetSplit.VALIDATION: 0,
        DatasetSplit.TEST: 1,
    }
    assert manifest.proportions[DatasetSplit.TRAIN] == Fraction(3, 4)
    # The pair, not `source` alone: three of ADR 0008's four classes share a
    # `source` value with another, so `source` alone merges two of them.
    assert manifest.provenance == {
        "first_party/photographed_owned_slab": 3,
        "contributed/contributed_under_written_grant": 1,
    }


def test_proportions_are_exact_fractions_and_never_rounded() -> None:
    manifest = _manifest(
        *(_member(f"00000000-0000-0000-0000-00000000000{digit}") for digit in "abc")
    )

    assert json.loads(render_manifest(manifest))["proportions"]["train"] == "1"
    assert manifest.proportions[DatasetSplit.TRAIN] == Fraction(1)


def test_an_empty_manifest_reports_zeros_rather_than_dividing_by_none() -> None:
    empty = _manifest()

    assert empty.counts == dict.fromkeys(DatasetSplit, 0)
    assert empty.proportions == dict.fromkeys(DatasetSplit, Fraction(0))
    assert empty.provenance == {}


# ---------------------------------------------------------------------------
# A member is only ever written by the transaction that creates the version
# ---------------------------------------------------------------------------
def test_nothing_here_adds_a_member_to_an_existing_version() -> None:
    """The inverse assertion, #153's style.

    `dataset_members` refuses an `UPDATE` in a trigger, but nothing in the schema
    stops an `INSERT` naming a version published last year. What stops it is that
    no function here does it — `create_version` writes the members it was handed,
    inside the transaction that created the version, and there is no second door.
    """
    source = Path(versioning.__file__ or "").read_text(encoding="utf-8")

    assert source.count("insert(dataset_members)") == 1
    assert not {
        name
        for name in dir(versioning)
        if not name.startswith("_") and name.startswith(("add_", "append_", "extend_"))
    }


def test_the_manifest_directory_is_the_repositorys_own() -> None:
    assert versioning.MANIFESTS_DIR == REPO_ROOT / "datasets" / "manifests"
    assert manifest_path(VERSION).name == f"{VERSION}.json"


# ---------------------------------------------------------------------------
# The command line refuses a moving pointer before the database has to
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "identifier",
    ["latest", "pokemon-condition-latest", "pokemon-condition", "Pokemon-Condition-v0.1.0", "v1"],
)
def test_a_version_that_is_not_explicit_and_ordered_is_refused(identifier: str) -> None:
    parser = versioning._parser()
    arguments = parser.parse_args(["--version", identifier, "--seed", "1"])

    with pytest.raises(SystemExit):
        versioning._validated(parser, arguments)


def test_publishing_without_a_seed_is_refused() -> None:
    """It is derivable from nothing, so it is chosen rather than defaulted."""
    parser = versioning._parser()

    with pytest.raises(SystemExit):
        versioning._validated(parser, parser.parse_args(["--version", VERSION]))


def test_regenerating_refuses_a_seed() -> None:
    """Re-splitting under a new seed is a different dataset wearing the same name."""
    parser = versioning._parser()
    arguments = parser.parse_args(["--version", VERSION, "--seed", "2", "--regenerate"])

    with pytest.raises(SystemExit):
        versioning._validated(parser, arguments)


def test_an_explicit_ordered_identifier_is_accepted() -> None:
    parser = versioning._parser()
    versioning._validated(parser, parser.parse_args(["--version", VERSION, "--seed", "1"]))
    versioning._validated(parser, parser.parse_args(["--version", VERSION, "--regenerate"]))


# ---------------------------------------------------------------------------
# Against a live database
# ---------------------------------------------------------------------------
def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """`test_migrations.py` leaves the database at `base`, so bring it up here."""
    if not DATABASE_URL:
        return
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(autouse=True)
def empty_tables() -> Iterator[None]:
    if not DATABASE_URL:
        yield
        return
    tables = "dataset_members, dataset_versions, training_images, physical_copies"
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def execute(statement: Any) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement)
        finally:
            await engine.dispose()

    run(scenario)


def store(*, copy: uuid.UUID | None = None, source: str = "first_party") -> uuid.UUID:
    """One provenance-clean training image."""
    image_id = uuid.uuid4()
    execute(
        sa.insert(training_images).values(
            id=image_id,
            physical_copy_id=copy,
            side="front",
            original_uri=f"training/{image_id}.png",
            sha256=f"{image_id.hex}{image_id.hex}",
            mime_type="image/png",
            width=756,
            height=1056,
            source=source,
            acquisition_method="photographed_owned_slab",
            license="owned outright",
            commercial_use_allowed=True,
            derivative_use_allowed=True,
            redistribution_allowed=False,
            acquired_at=datetime.now(UTC),
        )
    )
    return image_id


def copy_row() -> uuid.UUID:
    copy_id = uuid.uuid4()
    execute(sa.insert(physical_copies).values(id=copy_id))
    return copy_id


def publish(version: str, *, seed: int = SEED) -> Manifest:
    """What `run()` does for a publish, against the test's own engine."""

    async def scenario() -> Manifest:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                assignment = await split_corpus(connection, seed=seed)
                await create_version(connection, version=version, assignment=assignment)
                return await read_manifest(connection, version=version)
        finally:
            await engine.dispose()

    return run(scenario)


def regenerate(version: str) -> Manifest:
    async def scenario() -> Manifest:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await read_manifest(connection, version=version)
        finally:
            await engine.dispose()

    return run(scenario)


def corpus() -> tuple[CorpusImage, ...]:
    async def scenario() -> tuple[CorpusImage, ...]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return await read_corpus(connection)
        finally:
            await engine.dispose()

    return run(scenario)


def scalar(statement: Any) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return (await connection.execute(statement)).scalar_one()
        finally:
            await engine.dispose()

    return run(scenario)


@pytest.mark.integration
@requires_postgres
def test_a_version_has_its_members_its_splits_and_its_seed() -> None:
    stored = {store(copy=copy_row()) for _ in range(12)}

    manifest = publish(VERSION)

    assert manifest.version.version == VERSION
    assert manifest.version.ordinal == 1
    assert manifest.version.split_seed == SEED
    assert {member.training_image_id for member in manifest.members} == stored
    assert sum(manifest.counts.values()) == 12


@pytest.mark.integration
@requires_postgres
def test_regenerating_a_manifest_reproduces_it_byte_for_byte(tmp_path: Path) -> None:
    """The acceptance criterion. The manifest is a render of the rows, not a record."""
    for _ in range(9):
        store(copy=copy_row())

    first = write_manifest(publish(VERSION), tmp_path).read_bytes()
    second = write_manifest(regenerate(VERSION), tmp_path).read_bytes()

    assert first == second


@pytest.mark.integration
@requires_postgres
def test_an_annotated_member_carries_its_rows_and_regenerates_identically(
    tmp_path: Path,
) -> None:
    """#188: the annotation rows ride on the member, straight from the tables."""
    image_id = store(copy=copy_row())
    execute(
        sa.insert(image_annotations).values(
            id=uuid.uuid4(),
            training_image_id=image_id,
            kind="corner",
            region="top_left",
            label="whitening",
            severity="minor",
            confidence=0.9,
            representation="normalized",
            annotator_id="annotator",
        )
    )
    execute(
        sa.insert(centering_measurements).values(
            id=uuid.uuid4(),
            training_image_id=image_id,
            horizontal=0.55,
            vertical=0.5,
            confidence=0.9,
            annotator_id="annotator",
        )
    )

    manifest = publish(VERSION)

    (member,) = manifest.members
    (marker,) = member.annotations
    assert (marker.kind, marker.region, marker.label) == ("corner", "top_left", "whitening")
    assert marker.bbox is None
    (measurement,) = member.centering
    assert (measurement.horizontal, measurement.vertical) == (0.55, 0.5)
    first = write_manifest(manifest, tmp_path).read_bytes()
    second = write_manifest(regenerate(VERSION), tmp_path).read_bytes()
    assert first == second


@pytest.mark.integration
@requires_postgres
def test_the_recorded_seed_reproduces_the_split() -> None:
    """A split that cannot be re-derived makes a version reproducible in name only."""
    for _ in range(15):
        store(copy=copy_row())
    manifest = publish(VERSION)

    replayed = assign_splits(corpus(), seed=manifest.version.split_seed)

    assert {member.training_image_id: member.split for member in manifest.members} == dict(
        replayed.assignment
    )


@pytest.mark.integration
@requires_postgres
def test_a_frozen_version_refuses_an_update() -> None:
    store(copy=copy_row())
    publish(VERSION)

    with pytest.raises(DBAPIError) as refusal:
        execute(
            sa.update(dataset_versions)
            .where(dataset_versions.c.version == VERSION)
            .values(split_seed=1)
        )

    assert "dataset_versions is frozen" in str(refusal.value.orig)


@pytest.mark.integration
@requires_postgres
def test_a_frozen_member_refuses_an_update() -> None:
    store(copy=copy_row())
    publish(VERSION)

    with pytest.raises(DBAPIError) as refusal:
        execute(sa.update(dataset_members).values(split=str(DatasetSplit.TEST)))

    assert "dataset_members is frozen" in str(refusal.value.orig)


@pytest.mark.integration
@requires_postgres
def test_two_versions_over_overlapping_corpora_do_not_share_member_rows() -> None:
    """One image in two versions is two rows, and neither version can move the other's."""
    shared = [store(copy=copy_row()) for _ in range(6)]
    first = publish(VERSION)
    store(copy=copy_row())
    second = publish("pokemon-condition-v0.2.0", seed=SEED + 1)

    assert {member.training_image_id for member in first.members} == set(shared)
    assert set(shared) < {member.training_image_id for member in second.members}
    assert scalar(sa.select(sa.func.count()).select_from(dataset_members)) == 6 + 7
    assert first.version.id != second.version.id
    assert second.version.ordinal == first.version.ordinal + 1
    # The earlier version still says exactly what it said.
    assert render_manifest(first) == render_manifest(regenerate(VERSION))


@pytest.mark.integration
@requires_postgres
def test_a_name_is_published_once() -> None:
    store(copy=copy_row())
    publish(VERSION)

    with pytest.raises(IntegrityError) as conflict:
        publish(VERSION)

    assert versioning._VERSION_UNIQUE in str(conflict.value.orig)


@pytest.mark.integration
@requires_postgres
def test_an_empty_corpus_is_refused_and_writes_nothing() -> None:
    """A version with no members is a reference a training run resolves to nothing."""
    with pytest.raises(DatasetVersionRefused):
        publish(VERSION)

    assert scalar(sa.select(sa.func.count()).select_from(dataset_versions)) == 0


@pytest.mark.integration
@requires_postgres
def test_reading_a_version_nobody_published_is_refused() -> None:
    with pytest.raises(DatasetVersionRefused):
        regenerate(VERSION)
