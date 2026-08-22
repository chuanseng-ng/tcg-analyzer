"""Running the image pipeline's CV stages — issues #36, #37, #38, spec §18, §19.

The stages themselves are `tcg_ml_card_detection`, `tcg_ml_image_quality` and
`tcg_ml_normalization`, none of which knows anything about databases or object
storage. This module is the wiring: read what was stored, locate the card, judge
the photograph against spec §19, straighten the card into the standardized
artifact, write all of that back, and answer with what it means for the
analysis.

**Detection runs before the gate, which is not the order spec §18 draws.** §18
lists image quality above card detection, and that is still the order the
*refusal* happens in — the gate is the stage that stops an analysis, and the
detector never stops anything. But five of spec §19's eleven conditions are
about where the card is, so the boundary has to be found before the gate can
answer them. What §18 fixes is which stage owns the verdict, and that is
unchanged.

**Normalization runs last, and only sometimes.** It needs the quadrilateral, so
it cannot precede detection; and there is no point straightening a photograph
the gate has just refused, so it does not run for one. When no card was located
there is nothing to straighten and `normalized_uri` stays NULL — the honest
degradation, and the same one that leaves the gate's five geometric conditions
`undetermined`. A whole frame resized to the target would be a standardized
artifact of the table the card was lying on.

**Nothing imports this module eagerly, and that is load-bearing.**
`routers/analyses.py` imports `tcg_api.analysis.jobs` merely to enqueue, so a
module-level import of this file anywhere on that path would pull OpenCV into
the internet-facing API image — which is precisely what
`infrastructure/docker/worker.Dockerfile` exists to prevent. `jobs._advance`
imports it inside the function, and `tests/test_import_purity.py` asserts that
importing the application does not reach `cv2`. Do not "tidy" that import to the
top of `jobs.py`; the symptom is not a slow import, it is an API container that
will not start.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

import anyio.to_thread
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.analysis import ImageSide, QualityStatus
from tcg_domain.card_geometry import CardGeometry
from tcg_domain.image_quality import QualityReport, worst_status
from tcg_ml_card_detection import detect
from tcg_ml_image_quality import assess
from tcg_ml_normalization import MEDIA_TYPE, Normalized, normalize
from tcg_shared.storage.errors import StorageError
from tcg_shared.storage.keys import StorageKey, generate_key
from tcg_shared.storage.port import ObjectStorage

from tcg_api.analysis.images import read_v1_image_keys, record_normalization, record_quality
from tcg_api.storage import get_object_storage

__all__ = ["prepare_images"]

logger = structlog.get_logger(__name__)

#: Where normalized artifacts are stored. Its own prefix rather than the
#: upload's, so that an artifact is never mistaken for a photograph a user sent
#: — they have different retention consequences and only one of them is
#: irreplaceable.
NORMALIZED_NAMESPACE = "normalized"


async def prepare_images(db: AsyncSession, analysis_id: UUID) -> QualityStatus:
    """Judge every photograph of `analysis_id`, straighten it, and record both.

    Returns the analysis's verdict: the worst of its images', because spec §19's
    rules are about whether the *analysis* may proceed and one unusable
    photograph is enough to stop it.

    Does not commit — the caller owns the transaction, so the verdicts, the
    artifacts and the transition they cause land together.

    Raises:
        StorageError: If a stored image could not be read, or an artifact could
            not be written. Left to propagate: the job runner's retry and
            dead-letter path is the right place for an infrastructure failure,
            and a gate that swallowed one would pass photographs it had never
            seen.
        UnreadableImage: If a stored image does not decode, which means the
            object is not what its row says it is. Same treatment.
    """
    keys = await read_v1_image_keys(db, analysis_id)

    statuses: list[QualityStatus] = []
    for side in sorted(keys):
        # Built inside the loop, and deliberately not hoisted above it.
        # `get_object_storage` raises when the store is unconfigured, so
        # hoisting turns "this analysis has no photographs" — which folds to
        # `good` and needs no store at all — into a job failure about
        # configuration. It is `lru_cache`d, so the second side is free.
        storage = get_object_storage()
        report, artifact = await _prepare_one(storage, keys[side])
        await record_quality(db, analysis_id=analysis_id, side=side, report=report)
        statuses.append(report.status)
        # The verdict and the score, never the URI and never a measurement that
        # could describe the photograph itself (spec §54).
        logger.info(
            "image.assessed",
            analysis_id=str(analysis_id),
            side=str(side),
            quality_status=str(report.status),
            quality_score=round(report.score, 3),
            gate=report.version,
            # Whether the card was located at all, which is the difference
            # between five answered conditions and five undetermined ones — and
            # therefore the first thing to look at when a photograph nobody can
            # fault comes back `acceptable`.
            detector=report.detector,
        )
        if artifact is not None:
            await _store_artifact(
                db, storage, analysis_id=analysis_id, side=side, artifact=artifact
            )

    return worst_status(statuses)


async def _prepare_one(storage: ObjectStorage, key: str) -> tuple[QualityReport, Normalized | None]:
    """Fetch one stored photograph, judge it, and straighten it — off the event loop.

    Decoding tens of megapixels, walking its contours, convolving a Laplacian
    over the result and then warping it is CPU-bound, and a blocking call on the
    loop is an outage under load rather than a slow request — the same reason
    the upload endpoint puts `validate_image` behind `run_in_threadpool`. One hop
    for all three stages, not one each.
    """
    data = await storage.get(StorageKey(key))
    return await anyio.to_thread.run_sync(partial(_locate_judge_and_straighten, data))


def _locate_judge_and_straighten(data: bytes) -> tuple[QualityReport, Normalized | None]:
    """Where the card is, what spec §19 makes of it, and the artifact it yields.

    A detector that cannot find a card returns
    :class:`~tcg_domain.confidence.InsufficientInformation` rather than raising,
    and the gate reports the five geometric conditions undetermined with its
    reason. Undecodable bytes are the case all three stages meet: `detect` and
    `normalize` answer rather than raising, so `assess` stays the single place
    that turns them into an `UnreadableImage` for the job runner.

    The detection happens once. This is the only place the quadrilateral exists
    in memory, which is why normalization is here rather than in a second pass
    that would have to find the card again and could find a different one.

    ponytail: three decodes of the same JPEG, tens of milliseconds inside a
    background job. Pass a decoded array between the stages if a profile ever
    says it matters — the cost is that all three packages then take a numpy
    array instead of bytes, which is a worse contract for a worse reason.
    """
    geometry = detect(data)
    report = assess(data, geometry=geometry)
    if not isinstance(geometry, CardGeometry) or report.status is QualityStatus.UNUSABLE:
        return report, None

    artifact = normalize(data, geometry)
    return report, artifact if isinstance(artifact, Normalized) else None


async def _store_artifact(
    db: AsyncSession,
    storage: ObjectStorage,
    *,
    analysis_id: UUID,
    side: ImageSide,
    artifact: Normalized,
) -> None:
    """Write the artifact, then the row that names it.

    That order is the upload endpoint's, and for its reason: a committed row must
    always name bytes that are there.

    ponytail: the reverse failure is not handled. A job that dies between this
    `put` and `_advance`'s commit leaves an object no row points at, which the
    row-driven retention sweep cannot see — bounded at three per side by the
    task's retry limit, since each attempt mints a fresh key. Handling it means
    threading the written keys out to the one place that commits, which is a
    larger change than the leak is worth; sweep by prefix and age if it ever is.
    """
    key = generate_key(NORMALIZED_NAMESPACE)
    try:
        await storage.put(key, artifact.data, content_type=MEDIA_TYPE)
    except StorageError:
        logger.warning("image.artifact_could_not_be_stored", analysis_id=str(analysis_id))
        raise

    await record_normalization(
        db,
        analysis_id=analysis_id,
        side=side,
        normalized_uri=str(key),
        width=artifact.width,
        height=artifact.height,
        details=artifact.as_record(),
    )
    # The shape and the version, never the key and never anything about what the
    # photograph shows (spec §54).
    logger.info(
        "image.normalized",
        analysis_id=str(analysis_id),
        side=str(side),
        width=artifact.width,
        height=artifact.height,
        quarter_turns=artifact.quarter_turns,
        stage=artifact.version,
    )
