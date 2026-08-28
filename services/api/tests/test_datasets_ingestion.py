"""Ingesting an approved-source image through ADR 0008's gate — #154.

Spec §28 puts *approved data source* and *provenance verification* ahead of
ingestion, and #153 made the rights half of that a `CHECK`. This module tests
the half a constraint cannot carry: the four-source allow-list
`training_images.source` deliberately has no `CHECK` for, and a refusal whose
message names ADR 0008's rule rather than a constraint — because the caller is a
person with a directory of photographs, not a driver.

Two halves, on purpose. `verify_provenance` is pure, so its tests need nothing;
`ingest_training_image` writes a row and an object, so its tests need PostgreSQL
and use `InMemoryObjectStorage` — the same substitution
`test_image_upload_endpoint.py` makes, since nothing here exercises S3 semantics.

The database half is skipped unless `TCG_API_DATABASE_URL` points at a live
PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Final

import pytest
import sqlalchemy as sa
from PIL import Image
from PIL.ExifTags import GPS, IFD, Base
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.catalog.tables import cards, sets
from tcg_api.datasets.ingestion import (
    APPROVED_SOURCES,
    ProvenanceRefused,
    TrainingImageProvenance,
    _parser,
    _validated,
    ingest_training_image,
    verify_provenance,
)
from tcg_api.datasets.tables import PROVENANCE_FIELDS, physical_copies, training_images
from tcg_shared.storage.memory import InMemoryObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

SET_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
CARD_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")

#: The limits the CLI passes through from `Settings`, restated here so a test
#: that is not about a limit never meets one.
GENEROUS_BYTES: Final = 15 * 1024 * 1024
GENEROUS_PIXELS: Final = 50_000_000

SIZE: Final = (64, 48)

#: ADR 0008's primary class, complete. Every refusal test below spoils exactly
#: one field of this, so a failure names the rule that was broken.
FIRST_PARTY = TrainingImageProvenance(
    source="first_party",
    source_reference="PSA 12345678",
    acquisition_method="photographed_before_submission",
    license="owned outright",
    commercial_use_allowed=True,
    derivative_use_allowed=True,
    redistribution_allowed=False,
    permission_notes="ADR 0008 risk R1 — the artwork layer, by reference",
    acquired_at=datetime(2026, 8, 1, tzinfo=UTC),
)

#: The other three, as the research document fills §29 in for each.
OTHER_APPROVED = (
    replace(
        FIRST_PARTY,
        acquisition_method="photographed_owned_slab",
        source_reference="BGS 87654321",
    ),
    replace(
        FIRST_PARTY,
        source="contributed",
        acquisition_method="contributed_under_written_grant",
        source_reference="grant-2026-004",
        license="contributor grant grant-2026-004, 2026-08-14",
    ),
    replace(
        FIRST_PARTY,
        source="product_upload",
        acquisition_method="uploaded_by_user_with_consent",
        source_reference=str(uuid.uuid4()),
        license="consent text v1",
    ),
)


# ---------------------------------------------------------------------------
# Building photographs
# ---------------------------------------------------------------------------
# Copied rather than imported from `test_image_validation.py`: these modules are
# not an importable package, and `test_image_upload_endpoint.py` set the
# precedent of carrying its own small builder.
def a_picture() -> Image.Image:
    """Structure in it, so a lossy re-encode would be detectable."""
    picture = Image.new("RGB", SIZE, (200, 40, 40))
    for x in range(SIZE[0]):
        for y in range(SIZE[1]):
            picture.putpixel((x, y), ((x * 5) % 256, (y * 11) % 256, (x + y) % 256))
    return picture


def personal_metadata() -> Image.Exif:
    """EXIF of the kind a phone writes, including the field spec §54 cares most about."""
    exif = Image.Exif()
    exif[Base.Make] = "TestCam"
    exif[Base.Model] = "TestCam One"
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[GPS.GPSLatitudeRef] = "N"
    gps[GPS.GPSLatitude] = (1.0, 17.0, 3.0)
    gps[GPS.GPSLongitudeRef] = "E"
    gps[GPS.GPSLongitude] = (103.0, 51.0, 0.0)
    return exif


def a_photograph(colour: tuple[int, int, int] = (200, 40, 40), *, located: bool = False) -> bytes:
    picture = a_picture()
    picture.putpixel((0, 0), colour)
    buffer = BytesIO()
    if located:
        picture.save(buffer, "JPEG", quality=92, exif=personal_metadata())
    else:
        picture.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The allow-list and the gate, in code — no database needed
# ---------------------------------------------------------------------------
def test_the_allow_list_is_adr_0008s_four_classes_and_no_others() -> None:
    """Four, not three: two of them share `source` and differ by acquisition method.

    The schema deliberately carries no membership CHECK on `source`, so this
    tuple is the whole of the allow-list and a fifth entry is an ADR.
    """
    assert set(APPROVED_SOURCES) == {
        ("first_party", "photographed_before_submission"),
        ("first_party", "photographed_owned_slab"),
        ("contributed", "contributed_under_written_grant"),
        ("product_upload", "uploaded_by_user_with_consent"),
    }


@pytest.mark.parametrize("provenance", (FIRST_PARTY, *OTHER_APPROVED))
def test_every_approved_source_passes_the_gate(provenance: TrainingImageProvenance) -> None:
    verify_provenance(provenance)


@pytest.mark.parametrize("unknown", (None, False))
def test_an_unknown_commercial_use_is_refused_by_name(unknown: bool | None) -> None:
    """Spec §29 by name, and ADR 0008's "unknown is false".

    `None` and `False` are one answer here, exactly as the `IS TRUE` in the
    constraint makes them one answer there.
    """
    with pytest.raises(ProvenanceRefused, match="commercial_use_allowed"):
        verify_provenance(replace(FIRST_PARTY, commercial_use_allowed=unknown))


@pytest.mark.parametrize("unknown", (None, False))
def test_an_unknown_derivative_use_is_refused_by_name(unknown: bool | None) -> None:
    """A trained model is a derivative work, so this is gated beside commercial use."""
    with pytest.raises(ProvenanceRefused, match="derivative_use_allowed"):
        verify_provenance(replace(FIRST_PARTY, derivative_use_allowed=unknown))


@pytest.mark.parametrize("blank", (None, "", "   "))
def test_a_blank_licence_is_refused_by_name(blank: str | None) -> None:
    """ADR 0008's third answer: an empty string is not a licence.

    The code check mirrors `btrim(license) <> ''`, so whitespace is blank here
    too — a caller who typed a space must not get past a rule the database would
    have applied.
    """
    with pytest.raises(ProvenanceRefused, match="license"):
        verify_provenance(replace(FIRST_PARTY, license=blank))


def test_a_source_outside_the_allow_list_is_refused() -> None:
    """The half no constraint carries.

    `training_images.source` has no membership CHECK on purpose — the rights are
    enforced where they never change and the allow-list where it does, which is
    here.
    """
    with pytest.raises(ProvenanceRefused, match="ADR 0008"):
        verify_provenance(replace(FIRST_PARTY, source="hugging_face"))


def test_an_approved_source_with_the_wrong_acquisition_method_is_refused() -> None:
    """The pair is the class, not either half of it.

    A consented user upload cannot be relabelled as a photograph we took, and a
    photograph we took cannot borrow the consent class's licence.
    """
    with pytest.raises(ProvenanceRefused, match="ADR 0008"):
        verify_provenance(replace(FIRST_PARTY, acquisition_method="scraped_from_a_marketplace"))


def test_the_refusal_names_the_rule_rather_than_the_constraint() -> None:
    """A constraint violation is a correct refusal with a message nobody can act on.

    §28 puts provenance verification before ingestion for this reason, and the
    issue asks for it in as many words.
    """
    with pytest.raises(ProvenanceRefused) as refusal:
        verify_provenance(replace(FIRST_PARTY, commercial_use_allowed=None))

    message = str(refusal.value)
    assert "ADR 0008" in message
    assert "ck_training_images" not in message
    assert "CHECK" not in message


def test_the_code_gate_reads_exactly_the_columns_the_sql_gate_reads() -> None:
    """The two must not drift: one is the message, the other is the guarantee.

    Compared by value against the migration's own constant, which is the shape
    `test_datasets_tables.py` uses for the same reason.
    """
    from tcg_api.datasets.ingestion import GATED_FIELDS
    from tcg_api.datasets.tables import _PROVENANCE_GATE

    for field in GATED_FIELDS:
        assert field in _PROVENANCE_GATE
    assert set(GATED_FIELDS) == {
        "commercial_use_allowed",
        "derivative_use_allowed",
        "license",
    }
    # The one §29 right that is recorded and never gated.
    assert "redistribution_allowed" not in _PROVENANCE_GATE


def test_all_nine_provenance_fields_are_carried_and_no_tenth() -> None:
    """ADR 0008 says the per-copy identifier is deliberately not a tenth field."""
    from dataclasses import fields

    assert tuple(field.name for field in fields(TrainingImageProvenance)) == PROVENANCE_FIELDS


# ---------------------------------------------------------------------------
# Ingestion, against a real database
# ---------------------------------------------------------------------------
integration = [pytest.mark.integration, requires_postgres]


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
    tables = "dataset_members, dataset_versions, training_images, physical_copies, cards, sets"
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


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

    run(scenario)


def fetch(statement: Any) -> list[Any]:
    async def scenario() -> list[Any]:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return list((await connection.execute(statement)).all())
        finally:
            await engine.dispose()

    return run(scenario)


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


def ingest(
    storage: InMemoryObjectStorage,
    data: bytes,
    *,
    side: str = "front",
    provenance: TrainingImageProvenance = FIRST_PARTY,
    **keywords: Any,
) -> Any:
    async def scenario() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                return await ingest_training_image(
                    connection,
                    storage,
                    data=data,
                    side=side,
                    provenance=provenance,
                    max_bytes=GENEROUS_BYTES,
                    max_pixels=GENEROUS_PIXELS,
                    **keywords,
                )
        finally:
            await engine.dispose()

    return run(scenario)


def one_row(image_id: uuid.UUID) -> Any:
    rows = fetch(sa.select(training_images).where(training_images.c.id == image_id))
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize(
    "provenance", (FIRST_PARTY, *OTHER_APPROVED), ids=lambda p: p.acquisition_method
)
@pytest.mark.integration
@requires_postgres
def test_each_approved_source_is_ingested(
    storage: InMemoryObjectStorage, provenance: TrainingImageProvenance
) -> None:
    """One photograph per approved class, and all nine §29 fields on the row.

    The acceptance criterion, once for each class ADR 0008 admits.
    """
    ingested = ingest(storage, a_photograph(), provenance=provenance)

    row = one_row(ingested.id)
    for field in PROVENANCE_FIELDS:
        assert getattr(row, field) == getattr(provenance, field), field


@pytest.mark.integration
@requires_postgres
def test_the_stored_object_carries_no_exif(storage: InMemoryObjectStorage) -> None:
    """GPS included — spec §54's field, and the whole reason #33 strips before storing."""
    located = a_photograph(located=True)
    # Read outside the assert: `python -O` strips the statement, and a fixture
    # that quietly stopped carrying EXIF would make this test pass vacuously.
    with Image.open(BytesIO(located)) as submitted:
        carried = submitted.getexif()
    assert carried, "the fixture must carry EXIF to begin with"

    ingested = ingest(storage, located)
    stored = storage.objects[ingested.key].data

    with Image.open(BytesIO(stored)) as image:
        exif = image.getexif()
    assert not exif
    assert not exif.get_ifd(IFD.GPSInfo)


@pytest.mark.integration
@requires_postgres
def test_the_recorded_digest_is_over_the_stored_bytes(storage: InMemoryObjectStorage) -> None:
    """Not over the bytes that arrived — the strip changed them."""
    located = a_photograph(located=True)
    ingested = ingest(storage, located)
    stored = storage.objects[ingested.key].data

    assert ingested.sha256 == sha256(stored).hexdigest()
    assert ingested.sha256 != sha256(located).hexdigest()


@pytest.mark.integration
@requires_postgres
def test_the_recorded_dimensions_are_the_stored_images(storage: InMemoryObjectStorage) -> None:
    """`training_images.width`/`height` are NOT NULL, and this is where they come from."""
    ingested = ingest(storage, a_photograph())

    row = one_row(ingested.id)
    assert (row.width, row.height) == SIZE


@pytest.mark.integration
@requires_postgres
def test_a_file_that_is_not_a_jpeg_or_png_is_refused(storage: InMemoryObjectStorage) -> None:
    """The type is sniffed from the bytes; a `.jpg` extension claims nothing.

    A CLI has no `Content-Type` to distrust, so the equivalent of the issue's
    fourth named test is a file whose name says JPEG and whose bytes do not.
    """
    from tcg_api.analysis.image_validation import InvalidImage

    with pytest.raises(InvalidImage):
        ingest(storage, b"#!/bin/sh\nrm -rf /\n")

    assert not storage.objects


@pytest.mark.integration
@requires_postgres
def test_the_same_photograph_twice_is_refused_and_stores_nothing(
    storage: InMemoryObjectStorage,
) -> None:
    """The exact-duplicate half of deduplication, and all of it this issue owns.

    `uq_training_images_sha256` is the whole mechanism — the same photograph
    uploaded to two analyses is two `images` rows, and ingested twice is one
    training image. Left to the constraint rather than pre-checked, because "no
    deduplication beyond whatever a unique constraint gives for free" is an
    explicit non-goal.

    The second attempt stores **no object at all**, which is the point of
    writing the row before the bytes: the refusal costs nothing and leaves
    nothing to sweep up.
    """
    photograph = a_photograph()
    ingest(storage, photograph)

    with pytest.raises(IntegrityError, match="uq_training_images_sha256"):
        ingest(storage, photograph)

    assert len(fetch(sa.select(training_images))) == 1
    assert len(storage.objects) == 1


@pytest.mark.integration
@requires_postgres
def test_nothing_is_stored_when_the_provenance_is_refused(
    storage: InMemoryObjectStorage,
) -> None:
    """§28 verifies provenance *before* ingestion, so no bytes are ever written.

    Not merely cleaned up afterwards: an unapproved photograph must not reach
    object storage at all, even transiently.
    """
    with pytest.raises(ProvenanceRefused):
        ingest(storage, a_photograph(), provenance=replace(FIRST_PARTY, license=""))

    assert not storage.objects
    assert not fetch(sa.select(training_images))


@pytest.mark.integration
@requires_postgres
def test_a_consented_upload_records_no_physical_copy(storage: InMemoryObjectStorage) -> None:
    """Approved class 4 identifies no copy, and §32 falls back to grouping by `source`."""
    ingested = ingest(storage, a_photograph(), provenance=OTHER_APPROVED[2])

    assert one_row(ingested.id).physical_copy_id is None


@pytest.mark.integration
@requires_postgres
def test_two_sides_of_one_card_share_a_physical_copy(storage: InMemoryObjectStorage) -> None:
    """Spec §32: the front and back of one card must never split apart."""
    copy_id = uuid.uuid4()
    execute(sa.insert(physical_copies), {"id": copy_id})

    front = ingest(storage, a_photograph((1, 2, 3)), side="front", physical_copy_id=copy_id)
    back = ingest(storage, a_photograph((4, 5, 6)), side="back", physical_copy_id=copy_id)

    assert one_row(front.id).physical_copy_id == copy_id
    assert one_row(back.id).physical_copy_id == copy_id
    assert {one_row(front.id).side, one_row(back.id).side} == {"front", "back"}


@pytest.mark.integration
@requires_postgres
def test_a_catalog_card_can_be_recorded_and_can_be_absent(
    storage: InMemoryObjectStorage,
) -> None:
    """A directory can be ingested before anybody has identified what is in it."""
    seed_catalog()

    identified = ingest(storage, a_photograph((1, 2, 3)), card_id=CARD_ID)
    unidentified = ingest(storage, a_photograph((4, 5, 6)))

    assert one_row(identified.id).card_id == CARD_ID
    assert one_row(unidentified.id).card_id is None


@pytest.mark.integration
@requires_postgres
def test_the_storage_key_is_generated_and_carries_no_filename(
    storage: InMemoryObjectStorage,
) -> None:
    """ADR 0002 and spec §55: keys are server-side, and `generate_key` takes no name."""
    ingested = ingest(storage, a_photograph())

    uri = one_row(ingested.id).original_uri
    assert uri.startswith("training/")
    assert len(storage.objects) == 1


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
# Parsing and cross-field validation only — `run()` is exercised through
# `ingest_training_image` above, and `main()` is glue, as both existing CLIs'
# `main()` are.
FIRST_PARTY_ARGV: Final = (
    "--source",
    "first_party",
    "--acquisition-method",
    "photographed_before_submission",
    "--license",
    "owned outright",
    "--commercial-use-allowed",
    "--derivative-use-allowed",
    "--acquired-at",
    "2026-08-01T10:00:00+08:00",
)


def parse(*argv: str) -> Any:
    parser = _parser()
    arguments = parser.parse_args(list(argv))
    _validated(parser, arguments)
    return arguments


def a_file(tmp_path: Path, name: str = "front.jpg") -> str:
    path = tmp_path / name
    path.write_bytes(a_photograph())
    return str(path)


def test_a_first_party_card_parses(tmp_path: Path) -> None:
    arguments = parse("--front", a_file(tmp_path), *FIRST_PARTY_ARGV)

    assert arguments.commercial_use_allowed is True
    assert arguments.acquired_at.tzinfo is not None


def test_omitting_the_rights_leaves_them_unstated_for_adr_0008_to_refuse(
    tmp_path: Path,
) -> None:
    """Argparse must not be the one refusing this.

    "The following arguments are required" is the wrong message: the operator
    needs ADR 0008's, which is why the two booleans are optional here and
    default to None rather than being `required=True`.
    """
    arguments = parse(
        "--front",
        a_file(tmp_path),
        "--source",
        "first_party",
        "--acquisition-method",
        "photographed_before_submission",
        "--acquired-at",
        "2026-08-01T10:00:00+08:00",
    )

    assert arguments.commercial_use_allowed is None
    assert arguments.derivative_use_allowed is None
    with pytest.raises(ProvenanceRefused, match="ADR 0008"):
        verify_provenance(
            replace(
                FIRST_PARTY,
                commercial_use_allowed=arguments.commercial_use_allowed,
            )
        )


def test_redistribution_takes_no_flag() -> None:
    """ADR 0008 makes it false on all four approved sources; it is not a switch.

    No config for a value that never changes — a source that granted
    redistribution would be a new ADR, not a command-line argument.
    """
    with pytest.raises(SystemExit):
        _parser().parse_args(["--redistribution-allowed"])


def test_nothing_to_ingest_is_refused() -> None:
    with pytest.raises(SystemExit):
        parse(*FIRST_PARTY_ARGV)


def test_a_naive_acquired_at_is_refused(tmp_path: Path) -> None:
    """`acquired_at` is TIMESTAMP WITH TIME ZONE, and a photograph was taken somewhere."""
    with pytest.raises(SystemExit):
        parse(
            "--front",
            a_file(tmp_path),
            "--source",
            "first_party",
            "--acquisition-method",
            "photographed_before_submission",
            "--acquired-at",
            "2026-08-01T10:00:00",
        )


def test_half_a_certification_is_refused(tmp_path: Path) -> None:
    """`physical_copies` refuses it too; this is the message that says why."""
    with pytest.raises(SystemExit):
        parse(
            "--front",
            a_file(tmp_path),
            "--certification-company",
            "psa",
            *FIRST_PARTY_ARGV,
        )


def test_a_consented_upload_may_not_name_a_physical_copy(tmp_path: Path) -> None:
    """Approved class 4 identifies no copy — §32 groups it by `source` instead."""
    with pytest.raises(SystemExit):
        parse(
            "--front",
            a_file(tmp_path),
            "--physical-copy-id",
            str(uuid.uuid4()),
            "--source",
            "product_upload",
            "--acquisition-method",
            "uploaded_by_user_with_consent",
            "--license",
            "consent text v1",
            "--commercial-use-allowed",
            "--derivative-use-allowed",
            "--acquired-at",
            "2026-08-01T10:00:00+08:00",
        )


def test_a_photograph_that_is_not_a_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse("--front", str(tmp_path / "absent.jpg"), *FIRST_PARTY_ARGV)
