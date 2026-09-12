"""Ingesting a submission batch from a manifest — #311.

The `DTZ001` suppressions below are the assertions themselves: EXIF
`DateTimeOriginal` names no offset, and the whole point of `--timezone` is that
somebody has to supply one.

#154 deliberately deferred a manifest format: *"a schema, a parser and an error
class for a grouping the flags already express"*. #311's batch is what took it
up, because 112 cards per company is the shape of the first graded submission
and a card at a time is not it.

**The batch loops one card's transaction; it never widens it.** Everything the
single-card command refuses, this refuses, in the same place —
`tcg_api.datasets.ingestion.ingest_card` is the shared seam and
`validate_image` is still the only thing that decides what is storable.

What is new here, and what this module tests:

* a CSV whose columns pair two arbitrary filenames into one physical card;
* conversion of whatever Pillow can decode into the JPEG or PNG the corpus
  stores, leaving an already-acceptable file byte-identical;
* `acquired_at` read from EXIF **before** conversion, since a PNG carries none
  and `validate_image` strips what the original had.

The pure half needs nothing. The loop writes rows and objects, so it is marked
`integration` and skipped unless `TCG_API_DATABASE_URL` points at a live
PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import csv
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Final

import pytest
import sqlalchemy as sa
from PIL import Image
from PIL.ExifTags import Base
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.datasets.batch_ingestion import (
    HEIF_AVAILABLE,
    ManifestError,
    _parser,
    _validated,
    prepare_image,
    read_manifest,
    resolve_acquired_at,
)
from tcg_api.datasets.batch_ingestion import run as batch_run
from tcg_api.datasets.ingestion import APPROVED_SOURCES
from tcg_api.datasets.tables import training_images

DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to"
)
requires_storage = pytest.mark.skipif(
    not os.environ.get("TCG_API_STORAGE_ENDPOINT_URL"),
    reason="TCG_API_STORAGE_ENDPOINT_URL is unset; no live MinIO to put objects in",
)


REPO_ROOT = Path(__file__).resolve().parents[3]


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
    """Truncate before *and* after, which is `test_datasets_ingestion.py`'s shape.

    After matters as much as before: #196's guard counts corpus rows once, at
    session start, so a suite that left its own rows behind would fire it on the
    next local run against the same database and make a person prove the debris
    is theirs.
    """
    if not DATABASE_URL:
        yield
        return
    tables = "dataset_members, dataset_versions, training_images, physical_copies"
    _execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    _execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def _execute(statement: sa.TextClause) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(statement)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def _rows_for(copies: set[uuid.UUID]) -> list[sa.Row[tuple[object, ...]]]:
    """Every training image belonging to the copies a run reported."""

    async def read() -> list[sa.Row[tuple[object, ...]]]:
        engine = create_async_engine(DATABASE_URL or "", future=True)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    sa.select(training_images).where(training_images.c.physical_copy_id.in_(copies))
                )
                return list(result)
        finally:
            await engine.dispose()

    return asyncio.run(read())


GENEROUS_PIXELS: Final = 50_000_000
SIZE: Final = (64, 48)
EIGHT: Final = timezone(timedelta(hours=8))


def _image(
    fmt: str,
    *,
    taken: str | None = None,
    size: tuple[int, int] = SIZE,
    colour: tuple[int, int, int] = (200, 40, 40),
) -> bytes:
    """One small picture, optionally carrying EXIF DateTimeOriginal.

    **`colour` is not decoration.** `validate_image` strips EXIF before hashing,
    so two fixtures differing only in `taken` are one photograph as far as
    `uq_training_images_sha256` is concerned — which is correct of the corpus
    and useless as a fixture.
    """
    buffer = BytesIO()
    image = Image.new("RGB", size, colour)
    if taken is None:
        image.save(buffer, fmt)
    else:
        exif = image.getexif()
        exif[Base.DateTimeOriginal.value] = taken
        image.save(buffer, fmt, exif=exif)
    return buffer.getvalue()


def _manifest(tmp_path: Path, rows: list[dict[str, str]], *, header: list[str]) -> Path:
    path = tmp_path / "cards.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# The manifest: two arbitrary filenames are one physical card
# ---------------------------------------------------------------------------
def test_a_row_pairs_two_arbitrary_filenames_into_one_card(tmp_path: Path) -> None:
    (tmp_path / "IMG_4471.HEIC").write_bytes(b"front")
    (tmp_path / "DSC_0099.jpg").write_bytes(b"back")
    path = _manifest(
        tmp_path,
        [{"front": "IMG_4471.HEIC", "back": "DSC_0099.jpg", "label": "charizard-base-4"}],
        header=["front", "back", "label"],
    )

    (row,) = read_manifest(path)

    assert row.front == tmp_path / "IMG_4471.HEIC"
    assert row.back == tmp_path / "DSC_0099.jpg"
    assert row.label == "charizard-base-4"
    assert row.number == 1


def test_paths_resolve_against_the_manifest_not_the_working_directory(tmp_path: Path) -> None:
    """The operator runs this from anywhere; the photographs sit beside the CSV."""
    photographs = tmp_path / "batch-01"
    photographs.mkdir()
    (photographs / "a.png").write_bytes(b"x")
    path = _manifest(tmp_path, [{"front": "batch-01/a.png", "back": ""}], header=["front", "back"])

    (row,) = read_manifest(path)

    assert row.front == photographs / "a.png"
    assert row.back is None


def test_a_row_with_neither_side_is_refused_by_number(tmp_path: Path) -> None:
    path = _manifest(tmp_path, [{"front": "", "back": ""}], header=["front", "back"])

    with pytest.raises(ManifestError) as refusal:
        read_manifest(path)

    assert "row 1" in str(refusal.value)


def test_a_manifest_without_a_front_or_back_column_is_refused(tmp_path: Path) -> None:
    path = _manifest(tmp_path, [{"image": "a.png"}], header=["image"])

    with pytest.raises(ManifestError) as refusal:
        read_manifest(path)

    assert "front" in str(refusal.value)


def test_a_named_file_that_is_not_there_is_refused_before_anything_is_ingested(
    tmp_path: Path,
) -> None:
    path = _manifest(tmp_path, [{"front": "gone.png", "back": ""}], header=["front", "back"])

    with pytest.raises(ManifestError) as refusal:
        read_manifest(path)

    assert "gone.png" in str(refusal.value)


def test_an_empty_manifest_is_refused_rather_than_quietly_doing_nothing(tmp_path: Path) -> None:
    path = _manifest(tmp_path, [], header=["front", "back"])

    with pytest.raises(ManifestError):
        read_manifest(path)


def test_a_row_may_carry_its_own_acquired_at_and_card_id(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"x")
    card = uuid.uuid4()
    path = _manifest(
        tmp_path,
        [
            {
                "front": "a.png",
                "back": "",
                "card_id": str(card),
                "acquired_at": "2026-08-01T10:00:00+08:00",
            }
        ],
        header=["front", "back", "card_id", "acquired_at"],
    )

    (row,) = read_manifest(path)

    assert row.card_id == card
    assert row.acquired_at == datetime(2026, 8, 1, 10, 0, tzinfo=EIGHT)


def test_a_rows_acquired_at_must_name_its_offset(tmp_path: Path) -> None:
    """`acquired_at` is TIMESTAMPTZ; a naive one is not a fact about when."""
    (tmp_path / "a.png").write_bytes(b"x")
    path = _manifest(
        tmp_path,
        [{"front": "a.png", "back": "", "acquired_at": "2026-08-01T10:00:00"}],
        header=["front", "back", "acquired_at"],
    )

    with pytest.raises(ManifestError) as refusal:
        read_manifest(path)

    assert "row 1" in str(refusal.value)


# ---------------------------------------------------------------------------
# Conversion: an acceptable file is never re-encoded
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_a_jpeg_or_png_passes_through_byte_identical(fmt: str) -> None:
    """The corpus stores these two. Re-encoding one would change its digest for nothing."""
    data = _image(fmt)

    prepared = prepare_image(data, max_pixels=GENEROUS_PIXELS)

    assert prepared.data == data
    assert prepared.converted is False


def test_anything_else_pillow_can_decode_becomes_a_lossless_png() -> None:
    data = _image("WEBP")

    prepared = prepare_image(data, max_pixels=GENEROUS_PIXELS)

    assert prepared.converted is True
    with Image.open(BytesIO(prepared.data)) as converted:
        assert converted.format == "PNG"
        assert converted.size == SIZE


def test_a_file_no_decoder_here_recognises_names_the_extra_that_might_read_it() -> None:
    """Where a HEIC lands when `pillow-heif` is not installed.

    The API image carries no HEIF decoder on purpose, so this message is the
    whole of what an operator gets — it has to say what to install rather than
    let Pillow's own error out.
    """
    with pytest.raises(ManifestError) as refusal:
        prepare_image(b"not a picture at all", max_pixels=GENEROUS_PIXELS)

    assert "worker" in str(refusal.value)


def test_the_pixel_ceiling_is_applied_before_the_bitmap_is_decoded() -> None:
    """`validate_image`'s ordering, kept here because conversion decodes first."""
    data = _image("WEBP", size=(400, 400))

    with pytest.raises(ManifestError):
        prepare_image(data, max_pixels=1_000)


# ---------------------------------------------------------------------------
# acquired_at comes off the original, before conversion strips it
# ---------------------------------------------------------------------------
def test_exif_capture_time_is_read_from_the_original() -> None:
    data = _image("JPEG", taken="2026:08:01 10:14:22")

    prepared = prepare_image(data, max_pixels=GENEROUS_PIXELS)

    assert prepared.captured_at == datetime(2026, 8, 1, 10, 14, 22)  # noqa: DTZ001


def test_exif_survives_a_conversion_because_it_is_read_first() -> None:
    """A PNG carries none, so reading after converting would lose every timestamp."""
    data = _image("WEBP", taken="2026:08:01 10:14:22")

    prepared = prepare_image(data, max_pixels=GENEROUS_PIXELS)

    assert prepared.converted is True
    assert prepared.captured_at == datetime(2026, 8, 1, 10, 14, 22)  # noqa: DTZ001


def test_a_photograph_with_no_exif_reports_none_rather_than_now() -> None:
    prepared = prepare_image(_image("PNG"), max_pixels=GENEROUS_PIXELS)

    assert prepared.captured_at is None


def test_the_batch_timezone_is_attached_to_a_naive_exif_instant() -> None:
    captured = datetime(2026, 8, 1, 10, 14, 22)  # noqa: DTZ001 - EXIF names no offset

    when = resolve_acquired_at(row_acquired_at=None, captured_at=captured, offset=EIGHT)

    assert when == datetime(2026, 8, 1, 10, 14, 22, tzinfo=EIGHT)


def test_a_rows_own_acquired_at_beats_the_exif() -> None:
    stated = datetime(2026, 7, 4, 9, 0, tzinfo=UTC)

    when = resolve_acquired_at(
        row_acquired_at=stated,
        captured_at=datetime(2026, 8, 1, 10, 14, 22),  # noqa: DTZ001
        offset=EIGHT,
    )

    assert when == stated


def test_no_exif_and_no_stated_time_is_a_refusal_not_a_guess() -> None:
    with pytest.raises(ManifestError):
        resolve_acquired_at(row_acquired_at=None, captured_at=None, offset=EIGHT)


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
def test_the_timezone_flag_takes_an_offset_not_a_zone_name() -> None:
    arguments = _parser().parse_args(
        [
            "--manifest",
            "cards.csv",
            "--timezone",
            "+08:00",
            "--source",
            "first_party",
            "--acquisition-method",
            "photographed_before_submission",
            "--license",
            "owned outright",
            "--commercial-use-allowed",
            "--derivative-use-allowed",
        ]
    )

    assert arguments.timezone == EIGHT


def test_an_unstated_commercial_use_right_is_refused_before_a_file_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR 0008's refusal, and it happens once for the batch rather than per row."""
    parser = _parser()
    arguments = parser.parse_args(
        [
            "--manifest",
            str(tmp_path / "cards.csv"),
            "--timezone",
            "+08:00",
            "--source",
            "first_party",
            "--acquisition-method",
            "photographed_before_submission",
            "--license",
            "owned outright",
            "--derivative-use-allowed",
        ]
    )

    with pytest.raises(SystemExit):
        _validated(parser, arguments)

    assert "commercial" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# HEIC — the format this command exists for
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not HEIF_AVAILABLE, reason="pillow-heif is in the `worker` extra")
def test_a_heic_becomes_a_png_and_keeps_its_capture_time() -> None:
    """The phone's own file, ingested without the operator converting it first."""
    buffer = BytesIO()
    image = Image.new("RGB", SIZE, (200, 40, 40))
    exif = image.getexif()
    exif[Base.DateTimeOriginal.value] = "2026:08:01 10:14:22"
    image.save(buffer, "HEIF", exif=exif)

    prepared = prepare_image(buffer.getvalue(), max_pixels=GENEROUS_PIXELS)

    assert prepared.converted is True
    assert prepared.captured_at == datetime(2026, 8, 1, 10, 14, 22)  # noqa: DTZ001
    with Image.open(BytesIO(prepared.data)) as converted:
        assert converted.format == "PNG"
        assert converted.size == SIZE


# ---------------------------------------------------------------------------
# The loop, end to end
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.object_storage
@requires_postgres
@requires_storage
def test_a_batch_lands_every_row_and_a_rerun_skips_what_landed(tmp_path: Path) -> None:
    """The resumability claim, which is what makes 'log and continue' safe.

    `uq_training_images_sha256` is what does the work: the second run re-reads
    the same photographs, gets the same digests, and reports them as already
    present rather than writing a second copy of the corpus.
    """
    (tmp_path / "one-front.png").write_bytes(
        _image("PNG", taken="2026:08:01 10:14:22", colour=(10, 20, 30))
    )
    (tmp_path / "one-back.webp").write_bytes(
        _image("WEBP", taken="2026:08:01 10:14:40", colour=(40, 50, 60))
    )
    (tmp_path / "two-front.jpg").write_bytes(
        _image("JPEG", taken="2026:08:01 10:15:02", colour=(70, 80, 90))
    )
    manifest = _manifest(
        tmp_path,
        [
            {"front": "one-front.png", "back": "one-back.webp", "label": "card-one"},
            {"front": "two-front.jpg", "back": "", "label": "card-two"},
        ],
        header=["front", "back", "label"],
    )
    arguments = _parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--timezone",
            "+08:00",
            "--source",
            "first_party",
            "--acquisition-method",
            "photographed_before_submission",
            "--license",
            "owned outright",
            "--commercial-use-allowed",
            "--derivative-use-allowed",
        ]
    )

    outcome = asyncio.run(batch_run(arguments))

    assert (outcome.landed, outcome.refused, outcome.already_present) == (2, 0, 0)
    written = list(csv.DictReader((tmp_path / "cards.ingested.csv").read_text().splitlines()))
    assert [entry["label"] for entry in written] == ["card-one", "card-two"]
    assert all(entry["status"] == "landed" for entry in written)
    copies = {uuid.UUID(entry["physical_copy_id"]) for entry in written}
    assert len(copies) == 2, "each row is its own physical card"

    # The EXIF instant, with --timezone attached and nothing invented.
    stored = _rows_for(copies)
    assert len(stored) == 3, "two sides of card one, one of card two"
    # Compared as instants: `acquired_at` is TIMESTAMPTZ, so PostgreSQL keeps the
    # moment and hands it back in UTC. The offset was the input, never the column.
    #
    # Two instants for three photographs, and that is the design: `acquired_at`
    # belongs to `TrainingImageProvenance`, which is one card's. The front's EXIF
    # is the card's capture time and the back rides with it — exactly what the
    # single-card command does with one `--acquired-at` for both sides.
    assert {row.acquired_at.astimezone(UTC) for row in stored} == {
        datetime(2026, 8, 1, 10, 14, 22, tzinfo=EIGHT).astimezone(UTC),
        datetime(2026, 8, 1, 10, 15, 2, tzinfo=EIGHT).astimezone(UTC),
    }
    # The WEBP back was converted; the PNG and JPEG were not re-encoded.
    assert sorted(row.mime_type for row in stored) == ["image/jpeg", "image/png", "image/png"]

    again = asyncio.run(batch_run(arguments))

    assert (again.landed, again.refused, again.already_present) == (0, 0, 2)


@pytest.mark.integration
@pytest.mark.object_storage
@requires_postgres
@requires_storage
def test_one_bad_row_does_not_stop_the_batch(tmp_path: Path) -> None:
    """112 cards is too many to restart because row 40 has no EXIF."""
    (tmp_path / "good.png").write_bytes(
        _image("PNG", taken="2026:08:02 11:00:00", colour=(100, 110, 120))
    )
    (tmp_path / "undated.png").write_bytes(_image("PNG", colour=(130, 140, 150)))
    manifest = _manifest(
        tmp_path,
        [
            {"front": "undated.png", "back": "", "label": "no-exif"},
            {"front": "good.png", "back": "", "label": "fine"},
        ],
        header=["front", "back", "label"],
    )
    arguments = _parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--timezone",
            "+08:00",
            "--source",
            "first_party",
            "--acquisition-method",
            "photographed_before_submission",
            "--license",
            "owned outright",
            "--commercial-use-allowed",
            "--derivative-use-allowed",
        ]
    )

    outcome = asyncio.run(batch_run(arguments))

    assert (outcome.landed, outcome.refused, outcome.already_present) == (1, 1, 0)
    written = list(csv.DictReader((tmp_path / "cards.ingested.csv").read_text().splitlines()))
    assert [entry["status"] for entry in written] == ["refused", "landed"]
    assert written[0]["physical_copy_id"] == ""


def test_the_help_names_the_pairs_rather_than_two_independent_lists() -> None:
    """The single-card command's rule, and the batch inherits the flags verbatim."""
    help_text = " ".join(_parser().format_help().split())

    for source, method in APPROVED_SOURCES:
        assert f"{source}/{method}" in help_text
