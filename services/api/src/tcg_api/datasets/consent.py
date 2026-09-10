"""Keeping a photograph the user said we could keep — issue #148, ADR 0008.

ADR 0008 approves this product's own uploads as training-image source class 4
*where the user consented*, and records that the class "supplies nothing" until
two things exist: a consent mechanism with no accounts (spec §53) and a
retention exception (spec §54). This module is both, and the shape of it is
decided by one sentence in `docs/retention.md`: a photograph is retained
**because a row says so**, and *"must never happen because a retention sweep
skipped something"*.

So **nothing here is an exception to a sweep**. Consenting *copies* the
photograph: new bytes under the `training/` namespace, a new `training_images`
row carrying spec §29's nine fields filled at that moment from the grantor. The
photograph the analysis used is a different object under `uploads/` and is still
deleted with its session on day seven, along with the session row, the analysis
and the `images` row. `analysis/retention.py` and `analysis/orphans.py` are
untouched, and `SWEPT_NAMESPACES` is still `uploads` and `normalized`.

**The row has to carry everything at the moment of consent**, because the sweep
deliberately deletes the session — a per-browser identifier kept forever is what
spec §53 argues against — so there is nothing to look the photograph up through
afterwards. That is also why withdrawal is a bearer capability rather than a
login: :func:`tcg_api.codes.mint` produces the code, the user is shown it once,
and only its sha256 is stored. `tcg_api.codes` lives outside `tcg_api/feedback/`
precisely so this module can use it without breaching the import-purity wall
that keeps spec §68's reported grades out of the corpus.

Two orderings are load-bearing and are the ones not to "simplify":

* **Retaining is one unit that commits itself**, and discards every object it
  put if any part of it fails. `ingest_training_image` writes the row before the
  bytes, so a refusal leaves nothing stored; a failure *after* a `put` would
  otherwise leak an object under a namespace no sweep walks.
* **Withdrawing deletes objects before rows**, for `docs/retention.md`'s reason
  turned around: the row is the only pointer to its objects, and `training/` and
  `training-normalized/` are outside the orphan sweep, so an object whose row
  went first is one nothing will ever find again. Both keys go — the original
  *and* the normalized artifact, the pair every other deleter in this repository
  reads together.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import V1_SIDES, ImageSide
from tcg_shared.storage import StorageError, StorageKey
from tcg_shared.storage.port import ObjectStorage

from tcg_api.analysis.images import Photograph
from tcg_api.database import execute
from tcg_api.datasets.annotation import DatasetStoreUnavailable
from tcg_api.datasets.ingestion import TrainingImageProvenance, ingest_training_image
from tcg_api.datasets.tables import dataset_members, training_images

__all__ = [
    "ACQUISITION_METHOD",
    "CONSENT_TEXT",
    "CONSENT_VERSION",
    "PERMISSION_NOTES",
    "SOURCE",
    "NothingToRetain",
    "Withdrawn",
    "retain",
    "withdraw",
]

logger = logging.getLogger(__name__)

#: The two halves of `APPROVED_SOURCES`' class-4 key. Named here so the values
#: the route writes are the values `verify_provenance` admits, rather than two
#: string literals free to drift apart in a review nobody made.
SOURCE: Final = "product_upload"
ACQUISITION_METHOD: Final = "uploaded_by_user_with_consent"

#: Spec §29's `license` for this class **is the consent text, by version** — the
#: dataset schema says so in as many words. So the version moves whenever a
#: single word of :data:`CONSENT_TEXT` moves, and a row records which words its
#: grantor actually read. There is no way to re-ask, so there is no way to
#: upgrade a row to a later version: an older one stays what it says it is.
CONSENT_VERSION: Final = "user-upload-consent-v1.0.0"

#: What the user reads before the checkbox, in order, and the only copy of it.
#: Served by `GET /training-consent` rather than written into `apps/web`,
#: because the version recorded on the row has to be the words that were on the
#: screen — a client copy is a second answer free to drift from the first.
#:
#: ADR 0008's interpretive rule 1 is that **silence is not a grant**, and it
#: binds a document this project wrote as firmly as anyone else's. That is why
#: the third paragraph names derivative use outright instead of asking to
#: "improve the product": a trained model is a derivative work of what it was
#: trained on, and a consent that did not say so would not have granted it.
CONSENT_TEXT: Final[tuple[str, ...]] = (
    "This is optional. Saying no changes nothing about your analysis, and it costs you nothing.",
    "Either way, everything you give this product is deleted seven days after you started — "
    "unless you say yes here.",
    "If you say yes, we keep these photographs privately and use them to train and test the "
    "models that read a card's condition. A model trained on a photograph is something derived "
    "from it, and that is what we are asking for; we would rather say so than call it improving "
    "the product.",
    "We will not publish, share or sell them. No dataset built from them is ever published.",
    "Photograph the card, not your room. A photograph taken on a desk catches hands, mail and "
    "whatever is on the wall behind you, and we keep what you send us.",
    "We keep nothing that says who you are — no account, no email address, no name — so we "
    "cannot work out later which photographs were yours. That is why saying yes gives you a "
    "code.",
    "The code is the only way back, and it is shown once. Enter it on the withdrawal page and "
    "every photograph you gave us is deleted, except any already inside a published training "
    "set: those are fixed records of what a model learned from, and changing one would make a "
    "past result impossible to reproduce.",
)

#: Spec §29's `permission_notes`, identical on every row this module writes. The
#: withdrawal terms are here because the operator notes for approved class 3 put
#: its grant's clause 6 here, and ADR 0008's risk R1 is here for the reason that
#: file gives: the artwork is not the user's to grant either.
PERMISSION_NOTES: Final = (
    "Consented in the product under the version named in `license`. Withdrawal is by the code "
    "minted at that moment and shown once: every image it names that is not yet a member of a "
    "published dataset version is deleted, and one that is stays, because spec §31 makes a "
    "version immutable. ADR 0008's standing risk R1 — the artwork depicted — is not granted "
    "here and is not grantable by the person who consented."
)

#: The message `tcg_api.database.execute` raises a store failure with. One
#: sentence and no identifier: which store was unreachable is the route's
#: `details.reason`, and #148 shares the dataset domain's.
_UNREACHABLE: Final = "The dataset store could not be reached."


class NothingToRetain(ValueError):
    """Consent was given for an analysis that has stored no photograph.

    A refusal rather than a no-op answering 201: a code minted over nothing is a
    code that withdraws nothing, and the user would have written it down for no
    reason.
    """


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """What a withdrawal reached, and what it could not.

    `kept` is the count ADR 0008 and the class-3 grant template both warn about
    in advance: an image inside a published dataset version stays, because spec
    §31 makes that version an immutable record of what a model was trained on.
    """

    deleted: int
    kept: int


def _provenance(*, analysis_id: uuid.UUID, acquired_at: datetime) -> TrainingImageProvenance:
    """Spec §29's nine fields for one consented photograph.

    Every one of them is known at this moment, from the grantor, and none is
    inferred — which is ADR 0008's own description of what makes an approved
    source cheap to gate. `redistribution_allowed` is `False` here as it is on
    all four approved sources: the artwork in the photograph is not ours, and it
    is not the person who consented's either.
    """
    return TrainingImageProvenance(
        source=SOURCE,
        # Text, never a foreign key: spec §54 deletes the analysis on schedule
        # and this row outlives it. What it buys is the one grouping this class
        # has — the front and the back of one card, together.
        source_reference=str(analysis_id),
        acquisition_method=ACQUISITION_METHOD,
        license=CONSENT_VERSION,
        commercial_use_allowed=True,
        derivative_use_allowed=True,
        redistribution_allowed=False,
        permission_notes=PERMISSION_NOTES,
        acquired_at=acquired_at,
    )


async def retain(
    db: AsyncSession,
    storage: ObjectStorage,
    *,
    analysis_id: uuid.UUID,
    photographs: Mapping[ImageSide, Photograph],
    code_hash: str,
    max_bytes: int,
    max_pixels: int,
) -> tuple[uuid.UUID, ...]:
    """Copy this analysis's photographs into the corpus, and commit.

    Commits, unlike almost everything else reached from a route, and discards
    every object it stored if anything goes wrong. The alternative — handing
    keys back for a caller to compensate with — makes that caller responsible
    for an invariant this module is the only one in a position to state: an
    object under `training/` that no row names is invisible to both sweeps
    forever.

    Args:
        db: The session. Its transaction is committed here.
        storage: Where the originals are read from and the copies written.
        analysis_id: Recorded as spec §29's `source_reference`, as text.
        photographs: What `read_v1_photographs` returned. Iterated in `V1_SIDES`
            order, so the front is ingested first and a failure is reproducible.
        code_hash: The sha256 of the withdrawal code. The code itself never
            reaches this module.
        max_bytes: `TCG_API_UPLOAD_MAX_BYTES`, applied again on the way in.
        max_pixels: `TCG_API_UPLOAD_MAX_PIXELS`, likewise.

    Returns:
        The identifier of every `training_images` row written.

    Raises:
        NothingToRetain: If the analysis has stored no V1 photograph.
        IntegrityError: If one of these photographs is already in the corpus —
            `uq_training_images_sha256`, which is also what makes a second
            consent over one analysis a refusal rather than a second code.
        StorageError: If an original could not be read or a copy not written.
    """
    sides = [side for side in V1_SIDES if side in photographs]
    if not sides:
        raise NothingToRetain("This analysis has no photograph to keep.")

    connection = await db.connection()
    stored: list[StorageKey] = []
    identifiers: list[uuid.UUID] = []
    try:
        for side in sides:
            photograph = photographs[side]
            data = await storage.get(StorageKey(photograph.original_uri))
            ingested = await ingest_training_image(
                connection,
                storage,
                data=data,
                side=side.value,
                provenance=_provenance(analysis_id=analysis_id, acquired_at=photograph.created_at),
                withdrawal_code_hash=code_hash,
                max_bytes=max_bytes,
                max_pixels=max_pixels,
            )
            stored.append(ingested.key)
            identifiers.append(ingested.id)
        await db.commit()
    except BaseException:
        await db.rollback()
        for key in stored:
            await _discard(storage, key)
        raise

    logger.info("retained %d consented photographs for analysis %s", len(identifiers), analysis_id)
    return tuple(identifiers)


#: The two key columns, read together everywhere in this repository that deletes
#: an image. `normalized_uri` is written out of band by
#: `tcg-normalize-training-images` and lands under `training-normalized/`, which
#: #264's orphan sweep does not walk — so a withdrawal reading only the original
#: would leave an artifact nothing can ever reach again.
_KEY_COLUMNS: Final = (training_images.c.original_uri, training_images.c.normalized_uri)

#: An image a published version names cannot be deleted:
#: `dataset_members.training_image_id` is `RESTRICT`. Asked here as part of the
#: one read, rather than left to a `DELETE` that fails halfway and a message
#: that would have to guess why.
_PUBLISHED: Final = (
    sa.select(sa.literal(1))
    .select_from(dataset_members)
    .where(dataset_members.c.training_image_id == training_images.c.id)
    .exists()
)


async def withdraw(db: AsyncSession, storage: ObjectStorage, *, code_hash: str) -> Withdrawn | None:
    """Delete every photograph this code covers that no published version names.

    Objects before rows, and both keys per row — the module docstring says why
    that ordering is the whole correctness argument here.

    Returns:
        What was deleted and what was kept, or `None` when the code names no row
        at all. The caller answers one bare 404 for that: unknown, mistyped and
        already-withdrawn are the same fact from outside, and a page able to
        tell them apart would tell a guesser their code was real.
    """
    rows = (
        await execute(
            db,
            sa.select(training_images.c.id, *_KEY_COLUMNS, _PUBLISHED.label("published")).where(
                training_images.c.withdrawal_code_hash == code_hash
            ),
            unavailable=DatasetStoreUnavailable,
            message=_UNREACHABLE,
        )
    ).all()

    if not rows:
        return None

    removable = [row for row in rows if not row.published]
    if not removable:
        return Withdrawn(deleted=0, kept=len(rows))

    for row in removable:
        for key in (row.original_uri, row.normalized_uri):
            if key is not None:
                await storage.delete(StorageKey(key))

    await execute(
        db,
        sa.delete(training_images).where(training_images.c.id.in_([row.id for row in removable])),
        unavailable=DatasetStoreUnavailable,
        message=_UNREACHABLE,
    )
    await db.commit()

    logger.info("withdrew %d consented photographs", len(removable))
    return Withdrawn(deleted=len(removable), kept=len(rows) - len(removable))


async def _discard(storage: ObjectStorage, key: StorageKey) -> None:
    """Delete an object whose transaction did not commit, and never mask the real error."""
    try:
        await storage.delete(key)
    except StorageError:
        logger.warning("consented photograph orphaned in object storage: %s", key, exc_info=True)
