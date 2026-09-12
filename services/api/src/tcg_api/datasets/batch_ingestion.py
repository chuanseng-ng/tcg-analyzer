"""Ingesting a submission batch from a manifest — #311.

#154 left a note where this command now is: a manifest format was *"a schema, a
parser and an error class for a grouping the flags already express"*, and not
that issue's. #311 is the issue that needs it. ADR 0011 wants sixteen
test-split grades per company, which is about 112 graded cards, and a card at a
time is not the shape of that.

**This loops one card's transaction; it never widens it.** Every row calls
:func:`~tcg_api.datasets.ingestion.ingest_card`, so front and back still land
together or not at all, and a row that fails leaves the rows before it standing.
Nothing about what the corpus will accept is decided here — ADR 0008's gate and
`validate_image` are both downstream, unchanged and unbypassed.

Three things about this module are load-bearing:

* **Provenance is verified once, for the batch, before a file is opened.** The
  rights flags are batch-wide, so a blank `--license` is a refusal that costs no
  connection and reads no photograph. Per-row failures are a different thing and
  are logged and skipped.
* **Conversion happens here and nowhere else.** `validate_image` is #33's, is
  shared with the public upload endpoint, and still accepts exactly JPEG and
  PNG; widening it would widen what that endpoint takes from the internet. What
  this adds is a step *before* it: a file the operator's phone produced becomes
  one of those two, or the row is refused.
* **EXIF is read before conversion, never after.** A PNG carries none and
  `validate_image` strips what an original had, so a timestamp read at any later
  point is a timestamp that no longer exists.

The HEIF decoder rides in the `worker` extra rather than the base dependencies,
on the same principle the ml packages follow: the internet-facing API image has
no caller for it. Without it a HEIC row refuses by name instead of raising
Pillow's own error at the operator.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Final

from PIL import Image, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from tcg_shared.storage import ObjectStorage

from tcg_api.analysis.image_validation import InvalidImage
from tcg_api.config import Settings, get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.ingestion import (
    APPROVED_SOURCES,
    ProvenanceRefused,
    TrainingImageProvenance,
    ingest_card,
    verify_provenance,
)
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = [
    "HEIF_AVAILABLE",
    "BatchOutcome",
    "ManifestError",
    "ManifestRow",
    "PreparedImage",
    "main",
    "prepare_image",
    "read_manifest",
    "resolve_acquired_at",
    "run",
]

logger = logging.getLogger(__name__)

# Registered at import, because `Image.open` cannot recognise a HEIC until it
# is. Optional on purpose: `pillow-heif` rides in the `worker` extra, so the API
# image — which has no caller for this command — does not carry libheif. Absent,
# a HEIC row refuses through `prepare_image`'s own message, which names the
# extra, rather than raising Pillow's at the operator.
#: Whether this environment can read a HEIC at all. Annotated before the
#: branch rather than inside it: one name, two ways of arriving at it.
HEIF_AVAILABLE: bool
try:  # pragma: no cover - one branch runs per installed environment
    import pillow_heif
except ImportError:  # pragma: no cover - the API image, deliberately
    HEIF_AVAILABLE = False
else:  # pragma: no cover - the worker image and `uv sync --extra worker`
    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True

#: The two Pillow format names the corpus stores, and therefore the two this
#: module leaves alone. Kept as format names rather than media types because
#: that is what `Image.format` reports and what `validate_image` keys on.
_PASS_THROUGH: Final = frozenset({"JPEG", "PNG"})

#: EXIF's `DateTimeOriginal`. Spelled as the tag number because that is what
#: `Image.getexif()` is keyed by, and it is stable across every format that
#: carries EXIF at all.
_DATE_TIME_ORIGINAL: Final = 0x9003

#: EXIF writes an instant as `YYYY:MM:DD HH:MM:SS`, colons and all, and names no
#: offset. The offset is the operator's to supply — see `--timezone`.
_EXIF_FORMAT: Final = "%Y:%m:%d %H:%M:%S"

#: The manifest's two required columns, and the three optional ones.
_REQUIRED_COLUMNS: Final = ("front", "back")
_OPTIONAL_COLUMNS: Final = ("label", "card_id", "acquired_at")


class ManifestError(ValueError):
    """The batch cannot be read as asked.

    A `ValueError` because that is what rejecting malformed input is. Every
    message names the row it is about, because the reader is somebody holding a
    spreadsheet and a hundred photographs.
    """


@dataclass(frozen=True, slots=True)
class ManifestRow:
    """One physical card, as a line of the CSV names it.

    Args:
        number: The 1-based data row, so a refusal can be found in a
            spreadsheet. The header is not row 1.
        front: The front photograph, or `None` where the row names none.
        back: As `front`. At least one of the two is present; `read_manifest`
            refuses a row with neither.
        label: The operator's own name for the card. **Never stored** — it exists
            to key the output against whatever the batch was written from.
        card_id: The catalog card, where it is already known.
        acquired_at: An explicit capture instant, overriding the photograph's own
            EXIF. Always offset-aware.
    """

    number: int
    front: Path | None
    back: Path | None
    label: str | None = None
    card_id: uuid.UUID | None = None
    acquired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """One photograph, as the corpus will take it.

    Args:
        data: JPEG or PNG bytes. Byte-identical to the input where the input was
            already one of those.
        converted: Whether anything was re-encoded, so the run can say how many
            files it rewrote.
        captured_at: EXIF `DateTimeOriginal`, **naive**, read from the original
            bytes. `None` where the file carries none.
    """

    data: bytes
    converted: bool
    captured_at: datetime | None


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """What a run did, in the three numbers the exit code is decided from."""

    landed: int
    refused: int
    already_present: int


def read_manifest(path: Path) -> tuple[ManifestRow, ...]:
    """Parse the CSV into rows, or say which line is wrong.

    Paths resolve against the **manifest's own directory**, not the working
    directory: the operator runs this from wherever, and the photographs sit
    beside the file that lists them.

    Every refusal here happens before a database, an object store or a decoder
    is reached, so a typo in row 90 costs nothing but the reading of 89 lines.

    Raises:
        ManifestError: For a missing column, an empty file, a row naming neither
            side, a file that is not there, or a timestamp with no offset.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ManifestError(f"{path} cannot be read: {error}") from error

    reader = csv.DictReader(text.splitlines())
    columns = set(reader.fieldnames or ())
    missing = [name for name in _REQUIRED_COLUMNS if name not in columns]
    if missing:
        raise ManifestError(
            f"{path} needs a {' and a '.join(missing)} column; it has "
            f"{', '.join(sorted(columns)) or 'none'}. The header is "
            f"{','.join(_REQUIRED_COLUMNS + _OPTIONAL_COLUMNS)}, and the last three are optional."
        )

    rows = tuple(
        _row(number, entry, base=path.parent) for number, entry in enumerate(reader, start=1)
    )
    if not rows:
        raise ManifestError(f"{path} lists no cards; there is nothing to ingest.")
    return rows


def _row(number: int, entry: dict[str, str | None], *, base: Path) -> ManifestRow:
    front = _side(number, entry.get("front"), base=base, side="front")
    back = _side(number, entry.get("back"), base=base, side="back")
    if front is None and back is None:
        raise ManifestError(
            f"row {number} names neither a front nor a back; a card with no photograph "
            f"is a copy row nothing can be measured on."
        )
    return ManifestRow(
        number=number,
        front=front,
        back=back,
        label=_text(entry.get("label")),
        card_id=_card_id(number, entry.get("card_id")),
        acquired_at=_aware(number, entry.get("acquired_at")),
    )


def _side(number: int, value: str | None, *, base: Path, side: str) -> Path | None:
    name = _text(value)
    if name is None:
        return None
    path = base / name
    if not path.is_file():
        raise ManifestError(f"row {number}'s {side} names {name}, which is not a file in {base}.")
    return path


def _text(value: str | None) -> str | None:
    """A blank cell and an absent column are one answer, and it is `None`."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _card_id(number: int, value: str | None) -> uuid.UUID | None:
    raw = _text(value)
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise ManifestError(f"row {number}'s card_id is not a UUID: {raw!r}.") from error


def _aware(number: int, value: str | None) -> datetime | None:
    """A row's own `acquired_at`, which must name its offset.

    `training_images.acquired_at` is TIMESTAMP WITH TIME ZONE. A naive instant
    would be read as the server's idea of local time, which is not a fact about
    when a photograph was taken.
    """
    raw = _text(value)
    if raw is None:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ManifestError(
            f"row {number}'s acquired_at is not an ISO 8601 timestamp: {raw!r}."
        ) from error
    if when.tzinfo is None:
        raise ManifestError(
            f"row {number}'s acquired_at names no time zone: {raw!r}. Write e.g. "
            f"2026-08-01T10:00:00+08:00, or leave it blank and let --timezone answer."
        )
    return when


def prepare_image(data: bytes, *, max_pixels: int) -> PreparedImage:
    """Make one photograph storable, and read its capture time on the way past.

    A JPEG or a PNG comes back **byte-identical**: those are the two the corpus
    stores, and re-encoding one would change its digest — the corpus's identity
    for it — for nothing. Anything else Pillow can decode becomes a lossless
    PNG, which is the conversion `pokemon-condition-v0.2.0` was built with.

    The pixel ceiling is applied **before** `load()`, which is `validate_image`'s
    ordering and the whole defence against a decompression bomb: converting
    means decoding, so the check cannot wait for the caller downstream.

    Raises:
        ManifestError: If nothing here can decode the file, if it declares more
            than `max_pixels`, or if it stops making sense mid-decode.
    """
    if not data:
        raise ManifestError("the file is empty.")

    try:
        opened = Image.open(BytesIO(data))
    except UnidentifiedImageError as error:
        raise ManifestError(
            "no decoder here recognises this file. JPEG and PNG are stored as they are; "
            "HEIC needs the `worker` extra (uv sync --all-packages --extra worker)."
        ) from error
    except Image.DecompressionBombError as error:
        raise ManifestError(f"the image declares more than {max_pixels:,} pixels.") from error
    except (OSError, ValueError) as error:
        raise ManifestError("the file begins like an image and then stops making sense.") from error

    with opened as image:
        # Before `load()`, and before the EXIF read, so a bomb is refused while
        # it is still only a header.
        if image.width * image.height > max_pixels:
            raise ManifestError(f"the image is larger than {max_pixels:,} pixels.")

        captured_at = _captured_at(image)
        if image.format in _PASS_THROUGH:
            return PreparedImage(data=data, converted=False, captured_at=captured_at)

        try:
            image.load()
            # A palette or CMYK original has no lossless PNG of its own mode;
            # RGB is the one every downstream analyzer already expects.
            frame = image if image.mode in ("RGB", "RGBA", "L") else image.convert("RGB")
            buffer = BytesIO()
            frame.save(buffer, "PNG")
        except (OSError, ValueError) as error:
            raise ManifestError("the image could not be decoded.") from error

    return PreparedImage(data=buffer.getvalue(), converted=True, captured_at=captured_at)


def _captured_at(image: Image.Image) -> datetime | None:
    """EXIF `DateTimeOriginal`, naive, or `None` where the file carries none."""
    try:
        stamped = image.getexif().get(_DATE_TIME_ORIGINAL)
    except (OSError, ValueError, AttributeError):
        # A malformed EXIF block is a missing timestamp, never a failed ingest:
        # the photograph is still a photograph.
        return None
    if not isinstance(stamped, str):
        return None
    try:
        # DTZ007 is the point, not a slip: EXIF records no offset, so this
        # instant is naive by construction. `resolve_acquired_at` attaches
        # `--timezone`, and a default of UTC here would silently make every
        # Singapore photograph eight hours early.
        return datetime.strptime(stamped.strip(), _EXIF_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None


def resolve_acquired_at(
    *, row_acquired_at: datetime | None, captured_at: datetime | None, offset: timezone
) -> datetime:
    """Spec §29's `acquired_at` for one card, in precedence order.

    A row that states one wins: it is the operator answering directly. Otherwise
    the photograph's own EXIF instant gets the batch's offset attached, which is
    how `pokemon-condition-v0.2.0`'s twenty-eight were recorded.

    Raises:
        ManifestError: If neither exists. **Never `now()`** — the time a file was
            ingested is not the time a card was photographed, and a column that
            sometimes means one and sometimes the other means neither.
    """
    if row_acquired_at is not None:
        return row_acquired_at
    if captured_at is None:
        raise ManifestError(
            "no EXIF DateTimeOriginal and no acquired_at column; give the row an "
            "acquired_at with an offset, e.g. 2026-08-01T10:00:00+08:00."
        )
    return captured_at.replace(tzinfo=offset)


# ---------------------------------------------------------------------------
# The command line — `uv run tcg-ingest-training-batch`
# ---------------------------------------------------------------------------
# The provenance flags are the single-card command's, spelled the same way and
# refusing the same things, because they describe the batch rather than a card:
# one source, one licence, one pair of rights. What varies per card is what the
# manifest carries, and that is deliberately the short list.


def _offset(value: str) -> timezone:
    """`+08:00` — an offset, never a zone name.

    A name would need a database and would still be ambiguous across a DST
    boundary, which is exactly the hour a batch photographed in one sitting can
    straddle. An offset is what EXIF is missing and what the column stores.
    """
    try:
        parsed = datetime.fromisoformat(f"2026-01-01T00:00:00{value}")
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a UTC offset; write e.g. +08:00"
        ) from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{value!r} is not a UTC offset; write e.g. +08:00")
    shift = parsed.utcoffset() or timedelta(0)
    return timezone(shift)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help=(
            "the CSV listing one card per row: "
            f"{','.join(_REQUIRED_COLUMNS + _OPTIONAL_COLUMNS)} (the last three optional)"
        ),
    )
    parser.add_argument(
        "--timezone",
        required=True,
        type=_offset,
        metavar="OFFSET",
        help="the UTC offset EXIF does not record, e.g. +08:00",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="§29's source: " + ", ".join(sorted({source for source, _ in APPROVED_SOURCES})),
    )
    parser.add_argument(
        "--acquisition-method",
        required=True,
        help="§29's acquisition_method: " + ", ".join(method for _, method in APPROVED_SOURCES),
    )
    # Deliberately not `required`, for the single-card command's reason: argparse
    # saying "the following arguments are required" would be the wrong refusal.
    # ADR 0008's is the one the operator needs to read.
    parser.add_argument("--license", help="what permits the use (ADR 0008 refuses a blank one)")
    parser.add_argument(
        "--commercial-use-allowed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="omit to leave it unstated, which ADR 0008 refuses",
    )
    parser.add_argument(
        "--derivative-use-allowed",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="omit to leave it unstated, which ADR 0008 refuses",
    )
    parser.add_argument("--source-reference", help="the certification number, grant id or consent")
    parser.add_argument("--permission-notes", help="the grant's own limits, ADR 0008's risk R1")
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "where the card-to-copy-id mapping goes; defaults to <manifest>.ingested.csv "
            "beside the manifest. #311 keeps this outside the repository, with the photographs"
        ),
    )
    # No --certification-*: a batch of slabs would be one certification number
    # for every card in it, which is a row nobody can look up. Ingest those one
    # at a time, where the number belongs to the card in your hand.
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    """The refusals argparse cannot express, in the order they cost least."""
    try:
        verify_provenance(_provenance(arguments, acquired_at=datetime.now(arguments.timezone)))
    except ProvenanceRefused as refusal:
        # Once, for the batch. The rights flags describe every row, so refusing
        # here costs no connection and reads no photograph.
        parser.error(str(refusal))

    if not arguments.manifest.is_file():
        parser.error(f"--manifest names {arguments.manifest}, which is not a file")


def _provenance(arguments: argparse.Namespace, *, acquired_at: datetime) -> TrainingImageProvenance:
    return TrainingImageProvenance(
        source=arguments.source,
        source_reference=arguments.source_reference,
        acquisition_method=arguments.acquisition_method,
        license=arguments.license,
        commercial_use_allowed=arguments.commercial_use_allowed,
        derivative_use_allowed=arguments.derivative_use_allowed,
        # ADR 0008, on every approved source including our own photographs.
        redistribution_allowed=False,
        permission_notes=arguments.permission_notes,
        acquired_at=acquired_at,
    )


async def run(arguments: argparse.Namespace) -> BatchOutcome:
    """Ingest every row, and write what landed beside the manifest.

    One engine and one object store for the whole batch; one transaction per
    card. A row that fails is logged by number and skipped, because the
    alternative — stopping — leaves the operator to work out where to resume,
    and `uq_training_images_sha256` already makes a second run skip what landed.
    """
    rows = read_manifest(arguments.manifest)
    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    output = arguments.output or arguments.manifest.with_suffix(".ingested.csv")

    landed = refused = already_present = 0
    try:
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["row", "label", "front", "back", "status", "physical_copy_id"])
            # Flushed per row, so a run killed at card 90 still names the 89
            # copy identifiers whose grades will arrive weeks from now.
            for row in rows:
                status, copy_id = await _ingest_row(
                    engine, storage, row=row, arguments=arguments, settings=settings
                )
                writer.writerow(
                    [
                        row.number,
                        row.label or "",
                        row.front.name if row.front else "",
                        row.back.name if row.back else "",
                        status,
                        str(copy_id) if copy_id else "",
                    ]
                )
                handle.flush()
                if status == "landed":
                    landed += 1
                elif status == "already_present":
                    already_present += 1
                else:
                    refused += 1
    finally:
        await engine.dispose()

    logger.info("manifest written to %s", output)
    return BatchOutcome(landed=landed, refused=refused, already_present=already_present)


async def _ingest_row(
    engine: AsyncEngine,
    storage: ObjectStorage,
    *,
    row: ManifestRow,
    arguments: argparse.Namespace,
    settings: Settings,
) -> tuple[str, uuid.UUID | None]:
    """One card. Returns its status and, where it landed, its copy identifier."""
    max_pixels = settings.upload_max_pixels
    max_bytes = settings.upload_max_bytes
    try:
        front = _prepared(row.front, max_pixels=max_pixels)
        back = _prepared(row.back, max_pixels=max_pixels)
        # The front's EXIF is the card's capture time and the back rides with
        # it: `acquired_at` lives on `TrainingImageProvenance`, which is one
        # card's, and the single-card command gives both sides one
        # `--acquired-at` for the same reason. A back-only row uses its own.
        dated = front if front is not None else back
        acquired_at = resolve_acquired_at(
            row_acquired_at=row.acquired_at,
            captured_at=dated.captured_at if dated is not None else None,
            offset=arguments.timezone,
        )
    except ManifestError as refusal:
        logger.error("row %d refused: %s", row.number, refusal)
        return "refused", None

    for prepared, side in ((front, "front"), (back, "back")):
        if prepared is not None and prepared.converted:
            logger.info("row %d: %s converted to PNG", row.number, side)

    try:
        copy_id, ingested = await ingest_card(
            engine,
            storage,
            provenance=_provenance(arguments, acquired_at=acquired_at),
            front=front.data if front else None,
            back=back.data if back else None,
            card_id=row.card_id,
            max_bytes=max_bytes,
            max_pixels=max_pixels,
        )
    except (InvalidImage, ProvenanceRefused) as refusal:
        logger.error("row %d refused: %s", row.number, refusal)
        return "refused", None
    except IntegrityError as conflict:
        if "uq_training_images_sha256" in str(conflict.orig):
            logger.info("row %d is already in the corpus; a re-run skips what landed", row.number)
            return "already_present", None
        logger.error("row %d refused by the database: %s", row.number, conflict.orig)
        return "refused", None

    logger.info("row %d landed as copy %s (%d image(s))", row.number, copy_id, len(ingested))
    return "landed", copy_id


def _prepared(path: Path | None, *, max_pixels: int) -> PreparedImage | None:
    if path is None:
        return None
    try:
        return prepare_image(path.read_bytes(), max_pixels=max_pixels)
    except OSError as error:
        raise ManifestError(f"{path} cannot be read: {error}") from error


def main() -> int:
    """Console-script entry point (`uv run tcg-ingest-training-batch`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    try:
        outcome = asyncio.run(run(arguments))
    except ManifestError as refusal:
        logger.error("batch refused: %s", refusal)
        return 1

    logger.info(
        "batch finished: %d landed, %d already present, %d refused",
        outcome.landed,
        outcome.already_present,
        outcome.refused,
    )
    # Non-zero where anything was refused, so a scripted run notices. An
    # already-present row is not a failure: it is what a resumed run looks like.
    return 1 if outcome.refused else 0
