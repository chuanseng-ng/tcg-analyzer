"""The internal annotation surface — spec §30's tool, and nothing a consumer sees.

**Not part of spec §64.** §64's endpoints are the consumer product; this is the
internal surface `apps/annotation` reads, and ADR 0009 fixed both halves of what
that means. It is *in this application*, because §7 says not to create
unnecessary microservices in V1 and a second FastAPI application would duplicate
the error-envelope and migration wiring in order to enforce a boundary the
deployment already enforces. It is *in this schema*, because ADR 0001 makes the
OpenAPI document the only sanctioned way a TypeScript application learns an API
shape, and `apps/annotation` generates its types from it exactly as `apps/web`
does. What keeps it internal is neither of those: it is the `/internal` prefix,
which is what an ingress rule matches, and the tool being unroutable from the
public origin.

The router holds HTTP and nothing else — the statements live in
`tcg_api.datasets.annotation`, on `routers/grading.py`'s and `routers/cards.py`'s
rule.

**Three reads and one write**, and the write is append-only. There is
deliberately no edit endpoint: `trg_image_annotations_immutable` refuses an
`UPDATE`, so a correction is a new annotation rather than a `PATCH` this router
would have to explain to somebody who has just lost one.

Not rate-limited. Spec §55 names the analysis endpoints and the uploads, and ADR
0005 decided a read is neither — an internal tool behind its own ingress is
further from that reasoning still.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Final, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from tcg_domain.annotation import (
    NO_DEFECT_LABELS,
    AnnotationKind,
    CornerLabel,
    CornerRegion,
    DefectSeverity,
    EdgeLabel,
    EdgeRegion,
    SurfaceLabel,
)
from tcg_shared.storage import ObjectNotFound, StorageError
from tcg_shared.storage.port import ObjectStorage

from tcg_api.config import get_settings
from tcg_api.database import get_session_factory
from tcg_api.datasets.annotation import (
    ARTIFACT_MEDIA_TYPE,
    NORMALIZED,
    BoundingBox,
    CardFrame,
    CenteringReading,
    DatasetStoreUnavailable,
    DefectMarker,
    ImageAnnotations,
    StoredMarker,
    StoredMeasurement,
    TrainingImageDetail,
    TrainingImageSummary,
    read_annotations,
    read_bytes,
    read_image,
    read_work_list,
    record_annotations,
)
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse
from tcg_api.storage import get_object_storage

__all__ = [
    "AnnotationImageResponse",
    "AnnotationImageSummary",
    "AnnotationRequest",
    "AnnotationResponse",
    "AnnotationWorkListResponse",
    "CornerMarkerRequest",
    "EdgeMarkerRequest",
    "SurfaceMarkerRequest",
    "router",
]

#: structlog rather than the stdlib logger the other read-only routers use,
#: because this one logs a *value*. `ProcessorFormatter`'s chain carries no
#: `ExtraAdder`, so a stdlib `extra` mapping is silently dropped and the line
#: arrives with no identifier on it — which is the whole point of the one below.
#: `routers/analyses.py` and `routers/economics.py` log values the same way.
logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/annotation", tags=["internal: annotation"])

#: Repeated at the head of every route description. A reader arriving at
#: `/docs` from anywhere should not have to work out which of these is §64's.
_INTERNAL: Final = (
    "**Not part of spec §64.** The internal annotation surface (ADR 0009) — in "
    "this application and in this schema because `apps/annotation` generates its "
    "types from it, and kept off the public origin by deployment topology rather "
    "than by being a second service. "
)

_UNREACHABLE: Final = "The training image corpus could not be read."
_IMAGES_UNREACHABLE: Final = "The image store could not be reached."
_MISSING_OBJECT: Final = "The stored image could not be read."
_NO_SUCH_IMAGE: Final = "No such training image."
_NO_ARTIFACT: Final = (
    "No standardized artifact has been stored for this image, so a centering "
    "measurement, a corner or edge bounding box, or a surface annotation declaring "
    "'normalized' would be a claim about something that does not exist. Record markers "
    "without coordinates, declare surface work against the original photograph, or "
    "normalize the image first."
)

#: The bytes are an internal tool's, and an artifact is small enough that a
#: refetch on navigation costs nothing worth a caching bug. `no-store` rather
#: than a `max-age` for the reason `docs/development.md` gives about signed
#: URLs: the fewer copies of a training photograph exist outside the store, the
#: fewer places ADR 0008's withdrawal has to reach.
_CACHE_CONTROL: Final = "private, no-store"


class CardFrameModel(BaseModel):
    """Where the card sits inside the artifact, as fractions of the artifact.

    #194 surrounds the card with a margin of the photograph it was cut from, so
    the card's edges are an inner rectangle. The frame is derived from the
    artifact's own stored normalization record, so it is correct for whatever
    version produced that artifact — a pre-margin artifact honestly reports the
    whole unit square. **This is the detector's placement, a display reference**:
    centering is measured between two boxes the annotator draws — the card's
    outer edge traced against the margin, then the printed inner frame — because
    a border is a few percent of the card and a few pixels of quad error would
    swing the ratio wildly.
    """

    x: float = Field(description="The card's left edge, as a fraction of the artifact's width.")
    y: float = Field(description="The card's top edge, as a fraction of the artifact's height.")
    width: float = Field(description="The card's width, as a fraction of the artifact's width.")
    height: float = Field(description="The card's height, as a fraction of the artifact's height.")


class AnnotationImageSummary(BaseModel):
    """One training image, as the work list and an image's siblings report it."""

    id: UUID = Field(description="The training image's identifier.")
    side: str = Field(
        description=(
            "Which view of the card this is — spec §30's front/back, and the same "
            "vocabulary an uploaded analysis uses. Six values, not two: a corpus may "
            "hold angled and surface views of the same copy."
        ),
        examples=["front"],
    )
    card_id: UUID | None = Field(
        default=None,
        description="Which catalog card it depicts, or null where nobody has identified it.",
    )
    physical_copy_id: UUID | None = Field(
        default=None,
        description=(
            "Which physical object it is a photograph of. **Null is an honest answer**: "
            "a consented upload identifies no copy (ADR 0008's approved class 4)."
        ),
    )
    source: str = Field(
        description="Which ADR 0008 source class it came from.",
        examples=["first_party"],
    )
    created_at: datetime = Field(description="When the image was ingested.")
    has_artifact: bool = Field(
        description=(
            "Whether a standardized artifact has been stored for it. False means the "
            "normalization pass has not run, or found no card — the tool then shows the "
            "photograph and must say so, because a coordinate taken against a photograph "
            "is not comparable with one taken against an artifact. The storage key itself "
            "is deliberately not reported: it is server-generated and internal (spec §55)."
        )
    )
    card_frame: CardFrameModel | None = Field(
        default=None,
        description=(
            "Where the card sits inside the artifact — null exactly when "
            "`has_artifact` is false. On the summary and not only the detail, because "
            "the side toggle shows a sibling without navigating and one Save writes the "
            "image on screen: a centering reading taken there is measured against this "
            "rectangle, never against the artifact's edges (#194)."
        ),
    )


class AnnotationWorkListResponse(BaseModel):
    """The images awaiting annotation, one page at a time."""

    images: list[AnnotationImageSummary] = Field(description="This page of images, oldest first.")
    total: int = Field(
        description=(
            "How many images await annotation in total. **This number falls as "
            "annotations land**, so a page boundary can move underneath a client that "
            "is annotating while it pages."
        )
    )
    limit: int = Field(description="The page size that was applied.")
    offset: int = Field(description="The offset that was applied.")


class AnnotationImageResponse(BaseModel):
    """One training image, with the other photographs of the same physical copy."""

    id: UUID = Field(description="The training image's identifier.")
    side: str = Field(description="Which view of the card this is.", examples=["front"])
    card_id: UUID | None = Field(default=None, description="Which catalog card it depicts.")
    physical_copy_id: UUID | None = Field(
        default=None, description="Which physical object it is a photograph of."
    )
    source: str = Field(description="Which ADR 0008 source class it came from.")
    created_at: datetime = Field(description="When the image was ingested.")
    width: int = Field(description="The stored **photograph's** width in pixels.")
    height: int = Field(description="The stored **photograph's** height in pixels.")
    has_artifact: bool = Field(
        description=(
            "Whether a standardized artifact was stored, and therefore which "
            "representation `…/bytes` can serve. False means all there is to show is "
            "the photograph — and a tool showing it must label it, because coordinates "
            "cannot be taken against it. The same field every summary carries, so a "
            "detail is a summary with more on it rather than a second shape."
        )
    )
    card_frame: CardFrameModel | None = Field(
        default=None,
        description=(
            "Where the card sits inside the artifact — null exactly when "
            "`has_artifact` is false. The detector's placement, served as a reference; "
            "centering is measured between two annotator-drawn boxes, never against "
            "this rectangle or the artifact's edges (#194)."
        ),
    )
    siblings: list[AnnotationImageSummary] = Field(
        description=(
            "Other photographs of the same physical copy — what the front/back toggle "
            "moves between. **Empty when `physical_copy_id` is null**, which is an honest "
            "answer rather than a gap: treating null as a group would make every "
            "consented upload a sibling of every other one."
        )
    )
    annotations: list[StoredMarkerResponse] = Field(
        description=(
            "Every marker recorded against this image, oldest first. **Not collapsed to a "
            "current reading**: both annotation tables are append-only, so a correction is "
            "a newer row, and a surface has as many defects as it has. The work list "
            "excludes an annotated image, so this endpoint is the only way one is ever "
            "seen again."
        ),
    )
    centering: list[StoredMeasurementResponse] = Field(
        description="Every centering measurement recorded against this image, oldest first."
    )


class BoundingBoxModel(BaseModel):
    """Spec §17's bounding box, as fractions of the representation its marker names.

    **Fractions, never pixels.** The artifact's resolution is `ml/normalization`'s
    and appears nowhere in this service — a fraction survives a change to it, and
    a pixel would not. For a corner or edge the frame is always the artifact; a
    surface marker declares its own (#175, ADR 0010), and the unit-square rule
    below is the same in either.

    One object rather than four fields, because the schema's rule is
    `num_nulls(bbox_x, bbox_y, bbox_width, bbox_height) IN (0, 4)`: a box is whole
    or absent, and an optional object is that rule in a request body.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1, description="Distance from the left edge.", examples=[0.02])
    y: float = Field(ge=0, le=1, description="Distance from the top edge.", examples=[0.03])
    width: float = Field(gt=0, le=1, description="Width, and positive.", examples=[0.08])
    height: float = Field(gt=0, le=1, description="Height, and positive.", examples=[0.08])

    @model_validator(mode="after")
    def _lies_inside_the_frame(self) -> BoundingBoxModel:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("must lie inside the frame: x + width and y + height are at most 1")
        return self


class _MarkerRequest(BaseModel):
    """What every marker carries, whichever of §30's three features it is.

    The three kinds are three models rather than one with a `kind` field, and that
    is what puts **§14's, §15's and §16's lists into the OpenAPI schema separately**
    — which is the only sanctioned way `apps/annotation` learns them (ADR 0001).
    One model with `label: str` would have made the tool keep its own copy of
    twenty-eight strings, free to drift from `tcg_domain.annotation`, and would
    have let it offer `rough_cut` for a corner.

    The membership rules are therefore types here rather than validators, and only
    the severity pairing is left to assert. Every one of them is *also* a CHECK on
    `image_annotations`: **the CHECK is the guarantee and this is the message**
    (#154's rule) — refused here, an annotator is told which field is wrong;
    refused only by PostgreSQL, they get a 500.
    """

    #: A field this schema does not know is **refused, never dropped**. Two
    #: things would otherwise be accepted and silently discarded: a `region` on a
    #: surface, which §16 has no positions for, and an `annotator_id`, which spec
    #: §30 makes the service's. Both are better answered than ignored — a client
    #: that believes it set the annotator is worse off than one that is told it
    #: cannot.
    model_config = ConfigDict(extra="forbid")

    severity: DefectSeverity | None = Field(
        default=None,
        description=(
            "How bad it is — an **ordinal**, because there is one annotator and no "
            "agreement study, so finer granularity would record a precision nobody "
            "could reproduce. Null exactly when the label asserts no defect (`clean` "
            "found nothing to rate, `unknown` could not rate what it found), and "
            "required otherwise."
        ),
        examples=["minor"],
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "§30's uncertainty — how sure the annotator is of this call. **Required, "
            "with no default**: a default would read as certainty for every row nobody "
            "supplied one for, which is the fabricated confidence spec §2.7 forbids. "
            "The other half of the same rule is the `unknown` label every vocabulary "
            "carries."
        ),
        examples=[0.8],
    )
    bbox: BoundingBoxModel | None = Field(
        default=None,
        description=(
            "§17's spatial data, where the annotator drew it. Optional: a corner's "
            "region already names its position, so `top_left: clean` is a complete "
            "annotation. **Only meaningful against a stored artifact** — see the "
            "endpoint's 409."
        ),
    )

    def label_value(self) -> str:
        raise NotImplementedError  # pragma: no cover — every subclass overrides it

    def region_value(self) -> str | None:
        """Where on the card, or None for a surface, whose position is its box."""
        return None

    def representation_value(self) -> str:
        """Which frame the coordinates are fractions of.

        Always the artifact for a corner or an edge — ADR 0010 measured both
        adequate against it, and #175 changes the coordinate space of surface
        annotations only. A surface marker overrides this with its declaration.
        """
        return NORMALIZED

    def requires_artifact(self) -> bool:
        """Whether this marker is a claim about the standardized artifact.

        A corner or edge marker makes one exactly when it carries a box — its
        region already names its position. A surface marker overrides this: its
        representation is the claim, box or not.
        """
        return self.bbox is not None

    @model_validator(mode="after")
    def _a_defect_carries_a_severity(self) -> _MarkerRequest:
        """Mirror `ck_image_annotations_a_defect_carries_a_severity`.

        An equality between two facts rather than one implication, exactly as the
        CHECK is written: `clean` *with* a severity is as wrong as `chipping`
        without one, because a sound corner has nothing to rate.
        """
        asserts_no_defect = self.label_value() in NO_DEFECT_LABELS
        if asserts_no_defect and self.severity is not None:
            raise ValueError(f"{self.label_value()!r} asserts no defect, so it carries no severity")
        if not asserts_no_defect and self.severity is None:
            raise ValueError(f"{self.label_value()!r} is a defect, so it needs a severity")
        return self


class CornerMarkerRequest(_MarkerRequest):
    """One corner — spec §14. Four regions, not eight: the side is the image's."""

    kind: Literal[AnnotationKind.CORNER] = Field(
        description="Spec §30's corner annotation.", examples=["corner"]
    )
    region: CornerRegion = Field(
        description=(
            "Which corner. §14 lists eight, front- and back-prefixed; the prefix is "
            "`training_images.side`, because the image already knows which face it "
            "shows and naming it twice would let the two disagree."
        ),
        examples=["top_left"],
    )
    label: CornerLabel = Field(
        description=(
            "§14's eight potential labels. **Not §15's** — a corner cannot be "
            "`rough_cut` or `notching`, which are cutting defects of an edge."
        ),
        examples=["whitening"],
    )

    def label_value(self) -> str:
        return self.label.value

    def region_value(self) -> str | None:
        return self.region.value


class EdgeMarkerRequest(_MarkerRequest):
    """One edge — spec §15."""

    kind: Literal[AnnotationKind.EDGE] = Field(
        description="Spec §30's edge annotation.", examples=["edge"]
    )
    region: EdgeRegion = Field(description="Which edge, clockwise from the top.", examples=["left"])
    label: EdgeLabel = Field(
        description=(
            "§15's eight potential labels. **Not §14's** — an edge does not round "
            "or crease, and it does have `rough_cut` and `notching`."
        ),
        examples=["rough_cut"],
    )

    def label_value(self) -> str:
        return self.label.value

    def region_value(self) -> str | None:
        return self.region.value


class SurfaceMarkerRequest(_MarkerRequest):
    """One surface defect — spec §16.

    **No region field at all**, because §16 names no positions: a surface defect's
    position is its bounding box. And no `clean`, which is the specification's:
    a surface with nothing wrong is a surface nobody annotated, where a corner
    inspected and found sound is a row saying so.
    """

    kind: Literal[AnnotationKind.SURFACE] = Field(
        description="Spec §30's surface defect annotation.", examples=["surface"]
    )
    label: SurfaceLabel = Field(description="§16's twelve potential classes.", examples=["scratch"])
    representation: Literal["normalized", "original"] = Field(
        description=(
            "Which frame the coordinates are fractions of — 'normalized' (the "
            "standardized artifact) or 'original' (the photograph as ingested). "
            "ADR 0010 measured that the artifact cannot resolve §16's fine defect "
            "classes, so #175 lets a surface annotation — and only a surface — mark "
            "the original photograph. **Required, with no default**, for the reason "
            "`confidence` gives: a frame nobody named must be refused rather than "
            "read as a choice. Corners and edges carry no such field; theirs is "
            "always the artifact."
        ),
        examples=["original"],
    )

    def label_value(self) -> str:
        return self.label.value

    def representation_value(self) -> str:
        return self.representation

    def requires_artifact(self) -> bool:
        """Declaring 'normalized' is a claim about the artifact even without a
        box; declaring 'original' never needs one — the photograph always
        exists."""
        return self.representation == NORMALIZED


#: The three, discriminated on `kind`. A tagged union rather than a base model
#: with optional fields, so an invalid combination is not representable and the
#: generated TypeScript carries the same partition the specification does.
MarkerRequest = Annotated[
    CornerMarkerRequest | EdgeMarkerRequest | SurfaceMarkerRequest,
    Field(discriminator="kind"),
]


class CenteringReadingRequest(BaseModel):
    """One centering measurement — spec §21, §13.

    §13 requires ratios rather than qualitative labels, and the direction is
    stated once so a number cannot mean two things: `horizontal` is the **left**
    border as a fraction of the two side borders together, `vertical` the **top**
    of the two ends. `0.5` is perfect centering.

    The tool derives both from where the annotator put the inner frame, because
    an annotator typing a ratio is an annotator doing arithmetic under time
    pressure.
    """

    model_config = ConfigDict(extra="forbid")

    horizontal: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description=(
            "left / (left + right). **Null where the axis has no measurable border** — "
            "§21 names full-art and borderless layouts outright, and inventing 0.5 for "
            "one of them is the confidently-wrong output spec §2.7 exists to forbid."
        ),
        examples=[0.52],
    )
    vertical: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="top / (top + bottom), on the same terms.",
        examples=[0.49],
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "§30's uncertainty, required here exactly as on a marker. A border read off "
            "a worn or glare-lit edge is a real measurement with a low confidence, and "
            "recording it at 1.0 would be a fabricated certainty."
        ),
        examples=[0.9],
    )
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Free text — in practice, which of §21's awkward layouts this card is and "
            "what was measured against. Not one of §30's eleven and not a vocabulary: "
            "template awareness is M7's model, and this is the human's note to it."
        ),
    )

    @model_validator(mode="after")
    def _measures_something(self) -> CenteringReadingRequest:
        """Mirror `ck_centering_measurements_a_measurement_measures_something`."""
        if self.horizontal is None and self.vertical is None:
            raise ValueError(
                "a measurement measures at least one axis — a reading with neither "
                "records nothing while still taking the image off the work list"
            )
        return self


class AnnotationRequest(BaseModel):
    """One annotator's work on one image, written in one transaction.

    **One image per request, deliberately.** A marker belongs to the image whose
    artifact its coordinates are fractions of, and `training_images.side` is what
    says which face that is — accepting two images here would make it possible to
    file the back's corners against the front.

    Carries **no annotator and no timestamp**: spec §30 asks that both be recorded
    automatically rather than typed, so the service supplies them. That is also
    what puts `image_annotations.annotator_id`'s grammar out of a client's reach.

    Carries no `polygon` and no `metadata` either. Both are storable and read by
    nothing (#158); a polygon is in the same fractional space, so accepting one
    would mean it joined the artifact gate below for a control nothing draws yet.
    """

    model_config = ConfigDict(extra="forbid")

    markers: list[MarkerRequest] = Field(
        default_factory=list,
        description="Corner, edge and surface markers to record.",
    )
    centering: CenteringReadingRequest | None = Field(
        default=None,
        description="The centering measurement for this image, if one was taken.",
    )

    @model_validator(mode="after")
    def _records_something(self) -> AnnotationRequest:
        if not self.markers and self.centering is None:
            raise ValueError(
                "an annotation records at least one marker or a centering measurement — "
                "an empty one would take the image off the work list having said nothing"
            )
        return self

    def requires_artifact(self) -> bool:
        """Whether anything here is a claim about the standardized artifact.

        A centering ratio is read off where the card's borders sit in the artifact,
        so it is as much a coordinate as a bounding box is. A corner or edge marker
        with no box is not: its region names its position. A surface marker is
        whatever its representation declares — one naming 'original' never needs
        the artifact, because the photograph always exists (#175).
        """
        return self.centering is not None or any(
            marker.requires_artifact() for marker in self.markers
        )


class StoredMarkerResponse(BaseModel):
    """One marker as it was stored.

    Flat, where the request is a tagged union: the three kinds differ in what they
    *may* say, and a stored row has already said it. `region` is null for a
    surface, which is the same fact the union expresses by omitting the field.
    """

    id: UUID = Field(description="The annotation's identifier.")
    kind: str = Field(description="Corner, edge or surface.")
    region: str | None = Field(default=None, description="Where on the card, null for a surface.")
    label: str = Field(description="What was found.")
    severity: str | None = Field(default=None, description="How bad, null where nothing was rated.")
    confidence: float = Field(description="How sure the annotator was.")
    bbox: BoundingBoxModel | None = Field(default=None, description="§17's spatial data.")
    representation: str = Field(
        description=(
            "Which frame the coordinates are fractions of — 'normalized' or "
            "'original'. Always 'normalized' for a corner or an edge (#175)."
        )
    )
    annotator_id: str = Field(
        description="Who recorded it — supplied by the service, never by a client."
    )
    created_at: datetime = Field(description="§30's annotation timestamp.")


class StoredMeasurementResponse(BaseModel):
    """One centering measurement as it was stored."""

    id: UUID = Field(description="The measurement's identifier.")
    horizontal: float | None = Field(default=None, description="left / (left + right).")
    vertical: float | None = Field(default=None, description="top / (top + bottom).")
    confidence: float = Field(description="How sure the annotator was.")
    notes: str | None = Field(default=None, description="The annotator's note.")
    annotator_id: str = Field(description="Who recorded it.")
    created_at: datetime = Field(description="§30's annotation timestamp.")


class AnnotationResponse(BaseModel):
    """What one save wrote.

    **Oldest first and not collapsed to a current reading.** Both tables are
    append-only, so a correction is a newer row — but a surface has as many
    defects as it has, so no one collapsing rule fits all three kinds. The rows
    travel as they are.
    """

    markers: list[StoredMarkerResponse] = Field(description="The markers that were stored.")
    centering: list[StoredMeasurementResponse] = Field(
        description="The centering measurements that were stored."
    )


def _summary(image: TrainingImageSummary) -> AnnotationImageSummary:
    return AnnotationImageSummary(
        id=image.id,
        side=image.side,
        card_id=image.card_id,
        physical_copy_id=image.physical_copy_id,
        source=image.source,
        created_at=image.created_at,
        has_artifact=image.has_artifact,
        card_frame=_card_frame(image.card_frame),
    )


def _detail(image: TrainingImageDetail, stored: ImageAnnotations) -> AnnotationImageResponse:
    return AnnotationImageResponse(
        id=image.id,
        side=image.side,
        card_id=image.card_id,
        physical_copy_id=image.physical_copy_id,
        source=image.source,
        created_at=image.created_at,
        width=image.width,
        height=image.height,
        has_artifact=image.has_artifact,
        card_frame=_card_frame(image.card_frame),
        siblings=[_summary(sibling) for sibling in image.siblings],
        annotations=[_stored_marker(marker) for marker in stored.markers],
        centering=[_stored_measurement(measurement) for measurement in stored.centering],
    )


def _card_frame(frame: CardFrame | None) -> CardFrameModel | None:
    if frame is None:
        return None
    return CardFrameModel(x=frame.x, y=frame.y, width=frame.width, height=frame.height)


def _stored_marker(marker: StoredMarker) -> StoredMarkerResponse:
    return StoredMarkerResponse(
        id=marker.id,
        kind=marker.kind,
        region=marker.region,
        label=marker.label,
        severity=marker.severity,
        confidence=marker.confidence,
        bbox=None
        if marker.bbox is None
        else BoundingBoxModel(
            x=marker.bbox.x,
            y=marker.bbox.y,
            width=marker.bbox.width,
            height=marker.bbox.height,
        ),
        representation=marker.representation,
        annotator_id=marker.annotator_id,
        created_at=marker.created_at,
    )


def _stored_measurement(measurement: StoredMeasurement) -> StoredMeasurementResponse:
    return StoredMeasurementResponse(
        id=measurement.id,
        horizontal=measurement.horizontal,
        vertical=measurement.vertical,
        confidence=measurement.confidence,
        notes=measurement.notes,
        annotator_id=measurement.annotator_id,
        created_at=measurement.created_at,
    )


def _annotations(stored: ImageAnnotations) -> AnnotationResponse:
    return AnnotationResponse(
        markers=[_stored_marker(marker) for marker in stored.markers],
        centering=[_stored_measurement(measurement) for measurement in stored.centering],
    )


def _marker_of(
    marker: CornerMarkerRequest | EdgeMarkerRequest | SurfaceMarkerRequest,
) -> DefectMarker:
    """Turn a validated request marker into what the store takes.

    The enums become their values here rather than at the insert:
    `tcg_api.datasets.annotation` stores what the schema stores, which is text,
    and a `StrEnum` reaching a driver is a repr waiting to happen.
    """
    return DefectMarker(
        kind=marker.kind.value,
        region=marker.region_value(),
        label=marker.label_value(),
        severity=None if marker.severity is None else marker.severity.value,
        confidence=marker.confidence,
        bbox=None
        if marker.bbox is None
        else BoundingBox(
            x=marker.bbox.x,
            y=marker.bbox.y,
            width=marker.bbox.width,
            height=marker.bbox.height,
        ),
        representation=marker.representation_value(),
    )


def _centering_of(centering: CenteringReadingRequest) -> CenteringReading:
    return CenteringReading(
        horizontal=centering.horizontal,
        vertical=centering.vertical,
        confidence=centering.confidence,
        notes=centering.notes,
    )


def _corpus_unreachable() -> ApiError:
    """The 503 for a dataset store that is down or unconfigured.

    Its own `details.reason`, distinct from the analysis store's and the
    catalog's: an operator reading a 503 should be told which dependency is not
    answering rather than guessing.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "dataset_store_unreachable"},
    )


def _images_unreachable() -> ApiError:
    """The 503 for an object store that is down or unconfigured.

    `routers/analyses.py`'s reason string reused rather than a seventh invented:
    it is the same store failing in the same way, and two names for it would
    make a log harder to read rather than more precise.
    """
    return ApiError(
        ErrorCode.PROVIDER_ERROR,
        _IMAGES_UNREACHABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        details={"reason": "image_store_unreachable"},
    )


def _not_found() -> HTTPException:
    """The 404 for an image this corpus does not hold.

    FastAPI's own `HTTPException` and deliberately outside the §66 envelope, on
    `GET /analyses/{id}`'s reasoning: none of the eight codes means "not found",
    and `card_not_identified` is about a *card* rather than a precedent for one.
    The taxonomy stays closed at eight.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_IMAGE)


async def annotation_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session for one request. A dependency so tests can override it.

    Building the factory sits inside the guard on `analysis_session`'s reasoning:
    it reads `TCG_API_DATABASE_URL`, and an unset or malformed value should be the
    same 503 as an unreachable database rather than a 500.
    """
    try:
        factory = get_session_factory()
    except Exception as error:
        logger.warning("annotation.session_factory_unavailable", exc_info=True)
        raise _corpus_unreachable() from error

    async with factory() as session:
        yield session


async def object_storage() -> ObjectStorage:
    """Yield the object store for one request. A dependency so tests can override it."""
    try:
        return get_object_storage()
    except Exception as error:
        logger.warning("annotation.object_storage_unavailable", exc_info=True)
        raise _images_unreachable() from error


@router.get(
    "/images",
    response_model=AnnotationWorkListResponse,
    summary="List the training images awaiting annotation",
    description=(
        _INTERNAL + "Lists the training images that carry neither a defect marker nor a "
        "centering measurement, oldest first. Both tables are checked, not one: spec §30's "
        "eleven features are split across two of them, so an image carrying only a "
        "measurement has been worked on. Ordered by `(created_at, id)` — a total order, so "
        "paging neither drops nor duplicates a row. An offset past the end is an empty page, "
        "never a 404."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The corpus could not be read.",
        },
    },
)
async def list_images_awaiting_annotation(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200, description="How many images to return.")] = 50,
    offset: Annotated[int, Query(ge=0, description="How many to skip.")] = 0,
) -> AnnotationWorkListResponse:
    try:
        work = await read_work_list(db, limit=limit, offset=offset)
    except DatasetStoreUnavailable as error:
        raise _corpus_unreachable() from error

    response.headers["Cache-Control"] = _CACHE_CONTROL
    return AnnotationWorkListResponse(
        images=[_summary(image) for image in work.images],
        total=work.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/images/{image_id}",
    response_model=AnnotationImageResponse,
    summary="Read one training image and the other views of its copy",
    description=(
        _INTERNAL + "Returns one image, which representation can be shown for it, and the "
        "other photographs of the same physical copy — what a front/back toggle moves "
        "between. `siblings` is empty where the image names no physical copy, which is an "
        "honest answer rather than a gap."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No such training image."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The corpus could not be read.",
        },
    },
)
async def read_training_image(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    response: Response,
    image_id: Annotated[UUID, Path(description="The training image's identifier.")],
) -> AnnotationImageResponse:
    try:
        image = await read_image(db, image_id)
        if image is None:
            raise _not_found()
        stored = await read_annotations(db, image_id)
    except DatasetStoreUnavailable as error:
        raise _corpus_unreachable() from error

    response.headers["Cache-Control"] = _CACHE_CONTROL
    return _detail(image, stored)


@router.get(
    "/images/{image_id}/bytes",
    response_class=Response,
    summary="Serve one representation of a training image",
    description=(
        _INTERNAL + "Serves the bytes themselves, read through ADR 0002's `ObjectStorage` "
        "port. `representation=normalized` is the standardized artifact and 404s where none "
        "was stored — deliberately, rather than substituting the photograph: the caller has "
        "already been told which representation exists, and a silent substitution would hand "
        "a client a frame whose coordinates mean nothing. `Cache-Control: private, no-store`."
    ),
    responses={
        status.HTTP_200_OK: {
            "content": {"image/png": {}, "image/jpeg": {}},
            "description": "The stored bytes.",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "No such training image, or no such representation of it."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The corpus or the image store could not be reached.",
        },
    },
)
async def read_training_image_bytes(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    storage: Annotated[ObjectStorage, Depends(object_storage)],
    image_id: Annotated[UUID, Path(description="The training image's identifier.")],
    representation: Annotated[
        Literal["normalized", "original"],
        Query(description="Which representation to serve."),
    ] = "normalized",
) -> Response:
    try:
        stored = await read_bytes(db, storage, image_id, representation=representation)
    except DatasetStoreUnavailable as error:
        raise _corpus_unreachable() from error
    except ObjectNotFound as error:
        # The row names bytes the store does not hold. That will not come right
        # on a retry, so it is emphatically not a 503: it is the two stores
        # disagreeing, which is what `internal_error` means.
        logger.error("annotation.stored_object_missing", image_id=str(image_id))
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            _MISSING_OBJECT,
            details={"reason": "stored_object_missing"},
        ) from error
    except StorageError as error:
        raise _images_unreachable() from error

    if stored is None:
        raise _not_found()

    return Response(
        content=stored.data,
        media_type=stored.media_type or ARTIFACT_MEDIA_TYPE,
        headers={"Cache-Control": _CACHE_CONTROL},
    )


@router.post(
    "/images/{image_id}/annotations",
    response_model=AnnotationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record one annotator's work on one training image",
    description=(
        _INTERNAL + "Write spec §30's corner, edge and surface markers and the centering "
        "measurement for one image, in **one transaction**.\n\n"
        "**Append-only.** `trg_image_annotations_immutable` refuses an `UPDATE`, so there "
        "is no edit endpoint and there will not be one: a correction is a new annotation, "
        "and the current view of a corner is the newest row for it. Nothing is unique per "
        "image and per region, which is what makes that representable — and a surface has "
        "as many defects as it has.\n\n"
        "**The annotator and the timestamp are the service's.** §30 asks that both be "
        "recorded automatically rather than typed, so the request carries neither; the "
        "annotator comes from `TCG_API_ANNOTATOR_ID` and the timestamp from the row's "
        "default. That is also what keeps `annotator_id`'s grammar — which spec §53 makes "
        "structural, by having no `@` in it — out of a client's reach.\n\n"
        "**Claims about the artifact need the artifact.** A centering ratio, a corner or "
        "edge bounding box, and a surface annotation declaring 'normalized' are all "
        "claims about the standardized artifact; against a photograph no card was "
        "located in, they mean nothing, and sending one for an image whose "
        "`has_artifact` is false is a 409. A corner or edge marker with no box is still "
        "accepted there — its region names its position — and so is any surface marker "
        "declaring 'original': the photograph always exists, and ADR 0010 makes it the "
        "one frame that resolves §16's fine defect classes (#175).\n\n"
        "Recording anything takes the image off `GET /internal/annotation/images`."
    ),
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": (
                "No such training image. A bare body, deliberately outside the spec §66 "
                "envelope: none of the eight codes means 'not found'."
            )
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "The image has no stored artifact, and the annotation makes a claim "
                "about one — coordinates, or a surface declaration of 'normalized'. "
                "Also a bare body — §66 has no code for a conflict, and a ninth is not "
                "invented for this."
            )
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "The annotation is not one the schema would take: a label outside its "
                "kind's list, a region on a surface or missing from a corner, a defect "
                "with no severity or a `clean` with one, a box outside the unit square, "
                "a measurement of neither axis, or a request recording nothing at all. "
                "FastAPI's own validation body — §66 has no code for a malformed request."
            )
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The training image corpus could not be reached.",
        },
    },
)
async def record_image_annotations(
    db: Annotated[AsyncSession, Depends(annotation_session)],
    body: AnnotationRequest,
    image_id: Annotated[
        UUID,
        Path(description="The training image being annotated."),
    ],
) -> AnnotationResponse:
    """Write the markers and the measurement, then report what was stored."""
    image = await read_image(db, image_id)
    if image is None:
        raise _not_found()

    # One read answers both gates. Refusing the whole request where there is no
    # artifact would strand such an image at the head of the work list for ever;
    # refusing only the artifact claims leaves `top_left: clean` recordable — a
    # true thing to say about a photograph — and, since #175, every surface
    # marker declared against the original photograph.
    if not image.has_artifact and body.requires_artifact():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_ARTIFACT)

    try:
        stored = await record_annotations(
            db,
            image_id,
            markers=[_marker_of(marker) for marker in body.markers],
            centering=None if body.centering is None else _centering_of(body.centering),
            annotator_id=get_settings().annotator_id,
        )
        await db.commit()
    except DatasetStoreUnavailable as error:
        logger.warning("annotation.annotations_unwritable", exc_info=True)
        raise _corpus_unreachable() from error

    # Identifiers and counts. Never a label, a severity or the annotator's note:
    # what somebody wrote about a card is theirs, and a log is not the place for
    # it. structlog keywords rather than a stdlib `extra`, which this service's
    # `ProcessorFormatter` chain discards silently.
    logger.info(
        "annotation.annotations_recorded",
        image_id=str(image_id),
        marker_count=len(stored.markers),
        centering_recorded=bool(stored.centering),
    )
    return _annotations(stored)
