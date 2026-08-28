"""Ingesting one approved-source photograph — spec §28, #154.

Bytes plus a provenance record in; a stored object and a `training_images` row
out, or a refusal that says which rule was broken. This is the first thing that
writes into the dataset domain at all, and the first caller of the gate #153
made a `CHECK`.

**Almost none of the validation is here.** #33 already sniffs the type rather
than trusting a claim, applies the byte and pixel limits separately, reads the
dimensions from the header *before* decoding, and strips EXIF — GPS included —
losslessly; :func:`~tcg_api.analysis.image_validation.validate_image` is that
module's whole deliverable and is deliberately pure, so it is called here rather
than reimplemented. A second validation path is a divergence waiting to become a
bug, and the issue's acceptance criterion forbids one outright.

**Pillow only, and nothing from `ml/*`.** #155 owns running
`ml/normalization` on the artifact it needs, on demand, because it is the only
consumer when it lands. Normalizing at ingest would put OpenCV on this path for
a step nothing yet reads, and `test_import_purity.py` guards the API image
against exactly that.

Three things about this module are load-bearing:

* **Provenance is verified before anything is stored.** Spec §28's pipeline puts
  *approved data source* and *provenance verification* ahead of *ingestion*, and
  :func:`verify_provenance` is called before a single byte reaches object
  storage — not merely before the INSERT. An image nobody has the right to train
  on never exists anywhere, even transiently.
* **The code gate mirrors the SQL gate exactly, and does not replace it.** ADR
  0009's whole argument is that the rule is a constraint rather than a function a
  loader remembers to call, so the `CHECK` remains the guarantee. What this adds
  is a message a person can act on: a constraint violation is a correct refusal
  and a poor explanation, and the caller here is somebody with a directory of
  photographs. `is True` rather than truthiness, and `strip()` rather than a
  falsiness test, because `IS TRUE` and `btrim(license) <> ''` are what the
  database will apply.
* **The four-source allow-list lives here, and only here.** `training_images.source`
  carries no membership `CHECK` on purpose (`grading_rules.company`'s precedent):
  the *rights* are enforced in the schema, where they never change, and the
  allow-list in the ingestion path, where a fifth approved source costs an ADR
  and no migration.

**The row is written before the bytes are.** That is the opposite of #33's
order, and deliberately: an upload endpoint's session transaction already spans
other work, where a local ingest's does not. Inserting first means the gate,
`uq_training_images_sha256` and both foreign keys all refuse *before* anything
reaches object storage, so the commonest mistake — ingesting the same photograph
twice — costs nothing and leaves nothing behind. A storage failure then rolls the
row back, and the only orphan window left is a failure of the commit itself,
which :func:`run` compensates for.

An already-ingested photograph is a **refusal**, not a silent skip.
`uq_training_images_sha256` is the exact-duplicate half of deduplication and all
of it this issue owns — the near-duplicate half is #155's — and "no
deduplication beyond whatever a unique constraint gives for free" is an explicit
non-goal, so the constraint is left to do the whole job.

The command line is in this module rather than beside it, following
`tcg_api/grading/seed.py` and `tcg_api/catalog/import_catalog.py`: both keep
their `main()` in the module whose work they run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from tcg_shared.storage import StorageError, StorageKey, generate_key
from tcg_shared.storage.port import ObjectStorage

from tcg_api.analysis.image_validation import InvalidImage, validate_image
from tcg_api.config import get_settings
from tcg_api.database import create_engine
from tcg_api.datasets.tables import physical_copies, training_images
from tcg_api.logging import configure_logging
from tcg_api.storage import create_object_storage

__all__ = [
    "APPROVED_SOURCES",
    "GATED_FIELDS",
    "NAMESPACE",
    "IngestedImage",
    "ProvenanceRefused",
    "TrainingImageProvenance",
    "ingest_training_image",
    "main",
    "run",
    "verify_provenance",
]

logger = logging.getLogger(__name__)

#: The constraint a re-ingested photograph trips, so `main` can say what it
#: means rather than reflecting the driver's message back at the operator.
_SHA256_UNIQUE: Final = "uq_training_images_sha256"

#: The namespace every training image's key is generated under. Separate from
#: the analysis domain's `uploads`, because the two have different retention:
#: spec §54 sweeps an uploaded photograph away with its session, and a training
#: image outlives the analysis it may have been copied from.
NAMESPACE: Final = "training"

#: ADR 0008's four approved sources, as the `(source, acquisition_method)` pairs
#: the schema records them under, mapped to the register's own description of
#: each class. Four entries and three `source` values: classes 1 and 2 are both
#: photographs this project took, and what separates them is whether the card
#: was raw or already slabbed.
#:
#: **This tuple is the whole of the allow-list.** A fifth entry is an ADR — a
#: rejected source re-enters through the rubric in
#: `docs/training-image-provenance-research.md`, scored on the same scale, and
#: never by exception because a model wanted more data.
APPROVED_SOURCES: Final[dict[tuple[str, str], str]] = {
    ("first_party", "photographed_before_submission"): (
        "a raw card we own, photographed and then submitted for grading"
    ),
    ("first_party", "photographed_owned_slab"): "a graded slab we own, photographed",
    ("contributed", "contributed_under_written_grant"): (
        "contributed under a written grant naming commercial use, derivative use and retention"
    ),
    ("product_upload", "uploaded_by_user_with_consent"): (
        "this product's own user upload, where the user consented"
    ),
}

#: The three columns `ck_training_images_provenance_permits_training` reads, by
#: name. Declared so a test can hold this module and the migration's
#: `PROVENANCE_GATE` together — the two are one rule in two places, and the day
#: they disagree is the day a refusal stops matching its explanation.
#: `redistribution_allowed` is deliberately absent: ADR 0008 makes it false on
#: all four approved sources and the column records that rather than gating on
#: it.
GATED_FIELDS: Final = ("commercial_use_allowed", "derivative_use_allowed", "license")


class ProvenanceRefused(ValueError):
    """The image's provenance does not permit training on it — ADR 0008.

    A `ValueError` because that is what rejecting malformed input is. The
    message names the rule and the field, never the constraint: the reader is
    the person holding the photograph.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class TrainingImageProvenance:
    """Spec §29's nine fields, in §29's order.

    Keyword-only so the order survives `source_reference` and `permission_notes`
    being the two that are genuinely optional — §29 lists them where it lists
    them, and a dataclass would otherwise have to reorder the record to put the
    defaults last.

    **Nothing here defaults to a right.** `commercial_use_allowed` and
    `derivative_use_allowed` are `bool | None` with no default, so a caller who
    does not know has to say so; ADR 0008's rule is that a null, an empty string
    and an absent field are one answer, and it is refusal.
    `redistribution_allowed` has no default either, even though ADR 0008 makes it
    `false` on every approved source — a value that appeared because nobody chose
    it is the failure this milestone exists to prevent, whichever way it reads.
    """

    source: str
    source_reference: str | None = None
    acquisition_method: str
    license: str | None
    commercial_use_allowed: bool | None
    derivative_use_allowed: bool | None
    redistribution_allowed: bool
    permission_notes: str | None = None
    acquired_at: datetime


@dataclass(frozen=True, slots=True)
class IngestedImage:
    """What one photograph became.

    Args:
        id: The `training_images` row.
        key: Where the stored bytes went. Carried so the caller can discard the
            object if its own commit then fails.
        sha256: The digest over the bytes that were stored.
    """

    id: uuid.UUID
    key: StorageKey
    sha256: str


def verify_provenance(provenance: TrainingImageProvenance) -> None:
    """Refuse anything ADR 0008 does not admit, with a message naming the rule.

    Applied before ingestion, per spec §28's ordering. The `CHECK` on
    `training_images` remains the guarantee — this is the explanation.

    Raises:
        ProvenanceRefused: If the source is outside ADR 0008's four approved
            classes, or if commercial use, derivative use or the licence is
            anything other than a stated permission.
    """
    pair = (provenance.source, provenance.acquisition_method)
    if pair not in APPROVED_SOURCES:
        approved = ", ".join(f"{source}/{method}" for source, method in APPROVED_SOURCES)
        raise ProvenanceRefused(
            f"{provenance.source}/{provenance.acquisition_method} is not one of ADR 0008's "
            f"four approved training-image sources ({approved}). A rejected source re-enters "
            f"through the rubric in docs/training-image-provenance-research.md, never by "
            f"exception."
        )

    # `is True`, not truthiness — the same distinction `IS TRUE` draws in the
    # constraint, and the reason an unknown answer cannot pass as a permission.
    for field in ("commercial_use_allowed", "derivative_use_allowed"):
        stated = getattr(provenance, field)
        if stated is not True:
            right = field.removesuffix("_allowed").replace("_", " ")
            raise ProvenanceRefused(
                f"ADR 0008 admits an image only where {field} is true; it is {stated!r}. "
                f"An unstated {right} right is a refusal, not a maybe — spec §29 rejects "
                f"an image whose commercial-use status is unknown."
            )

    # `btrim(license) <> ''`: an empty string is not a licence, and neither is a
    # space somebody typed to get past a required field.
    if provenance.license is None or not provenance.license.strip():
        raise ProvenanceRefused(
            f"ADR 0008 requires a recorded licence, so license must state one — ownership, "
            f"the grant by identifier and date, or the consent text by version. It is "
            f"{provenance.license!r}, and a null, an empty string and whitespace are one answer."
        )


async def ingest_training_image(
    connection: AsyncConnection,
    storage: ObjectStorage,
    *,
    data: bytes,
    side: str,
    provenance: TrainingImageProvenance,
    physical_copy_id: uuid.UUID | None = None,
    card_id: uuid.UUID | None = None,
    max_bytes: int,
    max_pixels: int,
) -> IngestedImage:
    """Validate, verify, store and record one photograph.

    Args:
        connection: The caller's transaction. **Does not commit** — one card's
            photographs land together or not at all, which is what stops a
            half-ingested copy existing for §32 to group on.
        storage: Where the bytes go. The key is generated server-side.
        data: The photograph, as read from disk.
        side: One of `tcg_domain.analysis.ImageSide` — checked by the database
            rather than here, because that CHECK is the vocabulary's one home.
        provenance: Spec §29's nine fields.
        physical_copy_id: Which `physical_copies` row this is a photograph of.
            `None` for approved class 4, whose copies nothing identifies.
        card_id: The catalog card, where somebody has identified it.
        max_bytes: The largest file accepted, before anything is decoded.
        max_pixels: The largest bitmap accepted, read from the header.

    Raises:
        InvalidImage: If the file is empty, oversized, not a JPEG or PNG, or
            cannot be decoded.
        ProvenanceRefused: If ADR 0008 does not admit it.
        IntegrityError: If the photograph is already in the corpus
            (`uq_training_images_sha256`), or names a card or copy that is not.
    """
    if len(data) > max_bytes:
        # The analysis endpoint applies this while the body is still arriving;
        # a file on disk has already arrived, so the check is the comparison.
        # ponytail: 15 MiB is #33's ceiling for a phone upload, and a 45 MP DSLR
        # export can exceed it. Raise TCG_API_UPLOAD_MAX_BYTES for the run
        # rather than growing a second limit that can disagree about one picture.
        raise InvalidImage(f"The image is larger than {max_bytes:,} bytes.")

    validated = validate_image(data, max_pixels=max_pixels)

    # §28's order: verified before ingested, so an image nobody has the right to
    # train on never reaches object storage even transiently.
    verify_provenance(provenance)

    # The row first, so every remaining refusal — the gate, the duplicate digest,
    # a card or copy that does not exist — happens with nothing yet written.
    image_id = uuid.uuid4()
    key = generate_key(NAMESPACE)
    await connection.execute(
        sa.insert(training_images),
        {
            "id": image_id,
            "physical_copy_id": physical_copy_id,
            "card_id": card_id,
            "side": side,
            "original_uri": str(key),
            "sha256": validated.sha256,
            "mime_type": validated.mime_type,
            "width": validated.width,
            "height": validated.height,
            "source": provenance.source,
            "source_reference": provenance.source_reference,
            "acquisition_method": provenance.acquisition_method,
            "license": provenance.license,
            "commercial_use_allowed": provenance.commercial_use_allowed,
            "derivative_use_allowed": provenance.derivative_use_allowed,
            "redistribution_allowed": provenance.redistribution_allowed,
            "permission_notes": provenance.permission_notes,
            "acquired_at": provenance.acquired_at,
        },
    )
    # A failure here propagates and the caller's transaction rolls the row back,
    # so a row naming bytes nobody stored cannot survive.
    await storage.put(key, validated.data, content_type=validated.mime_type)

    return IngestedImage(id=image_id, key=key, sha256=validated.sha256)


async def _discard(storage: ObjectStorage, key: StorageKey) -> None:
    """Delete an object whose transaction did not commit, and never mask the real error."""
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("training image orphaned in object storage: %s", key, exc_info=True)


# ---------------------------------------------------------------------------
# The command line — `uv run tcg-ingest-training-images`
# ---------------------------------------------------------------------------
# One invocation is one physical card, because spec §32 requires the front and
# back of one copy to group together and never split across a train/test
# boundary. A directory is a shell loop over this command; a manifest format is
# not this issue's, and would be a schema, a parser and an error class for a
# grouping the flags already express.


def _aware(value: str) -> datetime:
    """An ISO 8601 instant that names its offset.

    `acquired_at` is TIMESTAMP WITH TIME ZONE, and a naive one would be read as
    the server's idea of local time — which is not a fact about when a
    photograph was taken.
    """
    try:
        when = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO 8601 timestamp") from error
    if when.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} names no time zone; write e.g. 2026-08-01T10:00:00+08:00"
        )
    return when


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--front", type=Path, help="the photograph of the card's front")
    parser.add_argument("--back", type=Path, help="the photograph of the card's back")
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
    # Deliberately not `required`, and deliberately defaulting to None. Argparse
    # saying "the following arguments are required" would be the wrong refusal:
    # ADR 0008's is, and it is the one the operator needs to read.
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
        "--acquired-at",
        required=True,
        type=_aware,
        metavar="ISO8601",
        help="when the photograph was taken, with an offset, e.g. 2026-08-01T10:00:00+08:00",
    )
    parser.add_argument(
        "--physical-copy-id",
        type=uuid.UUID,
        help=(
            "an existing physical_copies row to add these photographs to — how the "
            "post-grading photographs of a card join its pre-grading ones"
        ),
    )
    parser.add_argument("--certification-company", help="psa, tag or bgs, for a slab already owned")
    parser.add_argument("--certification-number", help="the number printed on the slab")
    parser.add_argument("--card-id", type=uuid.UUID, help="the catalog card, where it is known")
    # No --redistribution-allowed. ADR 0008 makes it false on all four approved
    # sources, including the photographs this project took itself, because the
    # artwork in them is not ours. The column records that answer; it is not a
    # switch to waive, and a source that granted redistribution would be an ADR.
    return parser


def _validated(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    if not arguments.front and not arguments.back:
        parser.error("give at least one of --front and --back; there is nothing to ingest")

    for flag in ("front", "back"):
        path = getattr(arguments, flag)
        if path is not None and not path.is_file():
            parser.error(f"--{flag} names {path}, which is not a file")

    if (arguments.certification_company is None) != (arguments.certification_number is None):
        parser.error(
            "--certification-company and --certification-number go together: half a "
            "certification is not a smaller certification, it is a row nobody can look up"
        )

    if arguments.physical_copy_id is not None and arguments.certification_company is not None:
        parser.error(
            "--physical-copy-id joins an existing copy and --certification-* describes a new "
            "one; write the certification onto the existing row instead"
        )

    if arguments.source == "product_upload" and arguments.physical_copy_id is not None:
        parser.error(
            "ADR 0008's approved class 4 identifies no physical copy — nothing in an "
            "anonymous session distinguishes two analyses of one card, so spec §32 groups "
            "these by source instead"
        )


async def _resolve_copy(
    connection: AsyncConnection, arguments: argparse.Namespace
) -> uuid.UUID | None:
    """Which `physical_copies` row these photographs belong to, creating one if needed.

    `None` only for approved class 4, whose copies nothing can identify.
    """
    if arguments.source == "product_upload":
        return None
    if arguments.physical_copy_id is not None:
        existing: uuid.UUID = arguments.physical_copy_id
        return existing

    copy_id = uuid.uuid4()
    await connection.execute(
        sa.insert(physical_copies),
        {
            "id": copy_id,
            "certification_company": arguments.certification_company,
            "certification_number": arguments.certification_number,
        },
    )
    return copy_id


async def run(arguments: argparse.Namespace) -> tuple[uuid.UUID | None, tuple[IngestedImage, ...]]:
    """Ingest one card's photographs in one transaction, and say what landed."""
    provenance = TrainingImageProvenance(
        source=arguments.source,
        source_reference=arguments.source_reference,
        acquisition_method=arguments.acquisition_method,
        license=arguments.license,
        commercial_use_allowed=arguments.commercial_use_allowed,
        derivative_use_allowed=arguments.derivative_use_allowed,
        # ADR 0008, on every approved source including our own photographs.
        redistribution_allowed=False,
        permission_notes=arguments.permission_notes,
        acquired_at=arguments.acquired_at,
    )
    # Refused before a database or an object store is even reached, so the
    # operator's typo costs no connection and writes nothing.
    verify_provenance(provenance)

    settings = get_settings()
    storage = create_object_storage(settings)
    engine = create_engine(settings)
    ingested: list[IngestedImage] = []
    try:
        async with engine.begin() as connection:
            copy_id = await _resolve_copy(connection, arguments)
            for side, path in (("front", arguments.front), ("back", arguments.back)):
                if path is None:
                    continue
                ingested.append(
                    await ingest_training_image(
                        connection,
                        storage,
                        data=path.read_bytes(),
                        side=side,
                        provenance=provenance,
                        physical_copy_id=copy_id,
                        card_id=arguments.card_id,
                        max_bytes=settings.upload_max_bytes,
                        max_pixels=settings.upload_max_pixels,
                    )
                )
    except BaseException:
        # The rows are gone with the transaction, so the objects those rows
        # named have to go too. Only reachable when a put succeeded and a later
        # statement — or the commit itself — did not.
        for image in ingested:
            await _discard(storage, image.key)
        raise
    finally:
        await engine.dispose()

    return copy_id, tuple(ingested)


def main() -> int:
    """Console-script entry point (`uv run tcg-ingest-training-images`)."""
    parser = _parser()
    arguments = parser.parse_args()
    _validated(parser, arguments)

    configure_logging(get_settings())

    try:
        copy_id, ingested = asyncio.run(run(arguments))
    except ProvenanceRefused as refusal:
        logger.error("training image refused: %s", refusal)
        return 1
    except InvalidImage as refusal:
        logger.error("training image refused: %s", refusal)
        return 1
    except IntegrityError as conflict:
        if _SHA256_UNIQUE in str(conflict.orig):
            logger.error(
                "this photograph is already in the training corpus; "
                "the digest is over the stored bytes, so a re-encode is a different image"
            )
        else:
            logger.error("training image refused by the database: %s", conflict.orig)
        return 1

    logger.info(
        "ingested %d training image(s) for physical copy %s: %s",
        len(ingested),
        copy_id or "none (approved class 4 identifies no copy)",
        ", ".join(str(image.id) for image in ingested),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
