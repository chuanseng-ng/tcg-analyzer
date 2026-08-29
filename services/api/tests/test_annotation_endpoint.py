"""The internal annotation surface — #159 and #160.

Integration rather than stubbed, and deliberately: the thing under test *is* the
predicate. "Awaiting annotation" is `NOT EXISTS` against two child tables, and a
stub repository would assert the stub. #160's writes are here for one more
reason: **the rules they enforce are CHECK constraints**, and the pydantic
validators only mirror them so an annotator gets a message rather than a 500 —
asserting the mirror against a stub would prove nothing about the schema. `test_analyses_endpoint.py`'s pattern —
a live PostgreSQL, `InMemoryObjectStorage` through the dependency override:

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.app import create_app
from tcg_api.datasets.tables import (
    centering_measurements,
    image_annotations,
    physical_copies,
    training_images,
)
from tcg_api.routers.annotation import object_storage
from tcg_shared.storage import ObjectNotFound, StorageError, StorageKey
from tcg_shared.storage.memory import InMemoryObjectStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

pytestmark = [pytest.mark.integration, requires_postgres]

WORK_LIST = "/internal/annotation/images"

#: Not a real PNG. Nothing here decodes one — the route reads bytes out of the
#: store and hands them back — and a fixture that needed OpenCV would put the CV
#: stack in a test of the module that must never reach it.
ARTIFACT_BYTES = b"\x89PNG\r\n\x1a\n artifact"
PHOTOGRAPH_BYTES = b"\xff\xd8\xff photograph"


def run(scenario: Callable[[], Awaitable[Any]]) -> Any:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
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
    tables = (
        "training_image_fingerprints, dataset_members, dataset_versions, "
        "image_annotations, centering_measurements, training_images, physical_copies, "
        "cards, sets"
    )
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield
    execute(sa.text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def execute(statement: Any, values: Any = None) -> None:
    async def scenario() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                if values:
                    await connection.execute(statement, values)
                else:
                    await connection.execute(statement)
        finally:
            await engine.dispose()

    run(scenario)


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.fixture
def client(storage: InMemoryObjectStorage) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[object_storage] = lambda: storage
    with TestClient(app) as opened:
        yield opened
    app.dependency_overrides.clear()


def a_copy() -> uuid.UUID:
    copy_id = uuid.uuid4()
    execute(sa.insert(physical_copies), {"id": copy_id})
    return copy_id


def an_image(
    storage: InMemoryObjectStorage,
    *,
    side: str = "front",
    copy_id: uuid.UUID | None = None,
    artifact: bool = True,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """One training image, its photograph, and optionally its artifact."""
    image_id = uuid.uuid4()
    original = f"training/2026/08/29/{image_id}"
    run(lambda: storage.put(StorageKey(original), PHOTOGRAPH_BYTES, content_type="image/jpeg"))

    normalized = None
    if artifact:
        normalized = f"training-normalized/2026/08/29/{image_id}"
        key = normalized
        run(lambda: storage.put(StorageKey(key), ARTIFACT_BYTES, content_type="image/png"))

    execute(
        sa.insert(training_images),
        {
            "id": image_id,
            "physical_copy_id": copy_id,
            "side": side,
            "original_uri": original,
            "normalized_uri": normalized,
            "normalization_details": (
                {
                    "width": 804,
                    "height": 1104,
                    "thresholds": {
                        "normalization_margin_mm": 2.0,
                        "normalization_pixels_per_mm": 12.0,
                    },
                }
                if artifact
                else None
            ),
            "sha256": f"{image_id.int:064x}"[:64],
            "mime_type": "image/jpeg",
            "width": 1200,
            "height": 1600,
            "source": "first_party",
            "acquisition_method": "photographed_before_submission",
            "license": "owned outright",
            "commercial_use_allowed": True,
            "derivative_use_allowed": True,
            "redistribution_allowed": False,
            "acquired_at": datetime(2026, 8, 1, tzinfo=UTC),
            **({"created_at": created_at} if created_at else {}),
        },
    )
    return image_id


def mark_a_defect(image_id: uuid.UUID) -> None:
    execute(
        sa.insert(image_annotations),
        {
            "id": uuid.uuid4(),
            "training_image_id": image_id,
            "kind": "corner",
            "region": "top_left",
            "label": "whitening",
            "severity": "minor",
            "confidence": 0.8,
            "representation": "normalized",
            "annotator_id": "annotator-1",
        },
    )


def measure_centering(image_id: uuid.UUID) -> None:
    execute(
        sa.insert(centering_measurements),
        {
            "id": uuid.uuid4(),
            "training_image_id": image_id,
            "horizontal": 0.52,
            "vertical": 0.49,
            "confidence": 0.9,
            "annotator_id": "annotator-1",
        },
    )


# ---------------------------------------------------------------------------
# The work list
# ---------------------------------------------------------------------------


def test_an_unannotated_image_is_on_the_work_list(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    body = client.get(WORK_LIST).json()

    assert body["total"] == 1
    assert [image["id"] for image in body["images"]] == [str(image_id)]
    assert body["images"][0]["has_artifact"] is True


def test_an_image_carrying_a_defect_marker_is_not_on_the_work_list(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    marked = an_image(storage)
    waiting = an_image(storage)
    mark_a_defect(marked)

    body = client.get(WORK_LIST).json()

    assert [image["id"] for image in body["images"]] == [str(waiting)]
    assert body["total"] == 1


def test_an_image_carrying_only_a_centering_measurement_is_not_on_the_work_list(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """The half a single anti-join against `image_annotations` would get wrong.

    Spec §30's eleven features are split across two tables (#158), so an image
    somebody has measured but not marked has been worked on. Putting it back in
    the queue would invite a second, contradictory reading of the same card.
    """
    measured = an_image(storage)
    waiting = an_image(storage)
    measure_centering(measured)

    body = client.get(WORK_LIST).json()

    assert [image["id"] for image in body["images"]] == [str(waiting)]
    assert body["total"] == 1


def test_the_work_list_pages_without_dropping_or_duplicating_a_row(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    for minute in range(5):
        an_image(storage, created_at=datetime(2026, 8, 1, 12, minute, tzinfo=UTC))

    first = client.get(WORK_LIST, params={"limit": 2, "offset": 0}).json()
    second = client.get(WORK_LIST, params={"limit": 2, "offset": 2}).json()
    third = client.get(WORK_LIST, params={"limit": 2, "offset": 4}).json()

    seen = [image["id"] for page in (first, second, third) for image in page["images"]]
    assert len(seen) == len(set(seen)) == 5
    assert first["total"] == 5


def test_an_offset_past_the_end_is_an_empty_page_of_a_non_empty_queue(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Never a 404, and never a total of zero — `GET /cards/search`'s rule."""
    an_image(storage)

    body = client.get(WORK_LIST, params={"offset": 50}).json()

    assert body["images"] == []
    assert body["total"] == 1


def test_the_work_list_is_never_cached(client: TestClient, storage: InMemoryObjectStorage) -> None:
    an_image(storage)

    assert client.get(WORK_LIST).headers["Cache-Control"] == "private, no-store"


# ---------------------------------------------------------------------------
# One image and its siblings
# ---------------------------------------------------------------------------


def test_an_image_reports_the_other_views_of_its_copy(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    copy_id = a_copy()
    front = an_image(storage, side="front", copy_id=copy_id)
    back = an_image(storage, side="back", copy_id=copy_id)

    body = client.get(f"{WORK_LIST}/{front}").json()

    assert body["has_artifact"] is True
    assert [sibling["id"] for sibling in body["siblings"]] == [str(back)]
    assert body["siblings"][0]["side"] == "back"


def test_an_image_naming_no_physical_copy_has_no_siblings(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """NULL is an honest answer, not a group.

    ADR 0008's approved class 4 identifies no copy, so treating NULL as a group
    would make every consented upload a sibling of every other one — and the
    front/back toggle would offer somebody else's card.
    """
    lonely = an_image(storage, copy_id=None)
    an_image(storage, copy_id=None)
    an_image(storage, copy_id=None)

    body = client.get(f"{WORK_LIST}/{lonely}").json()

    assert body["siblings"] == []
    assert body["physical_copy_id"] is None


def test_an_image_with_no_artifact_says_so_on_the_detail_too(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """A detail is a summary with more on it — one shape, one field, one answer."""
    image_id = an_image(storage, artifact=False)

    body = client.get(f"{WORK_LIST}/{image_id}").json()

    assert body["has_artifact"] is False
    assert body["width"] == 1200  # the photograph's, not the artifact's
    assert body["height"] == 1600


def test_the_detail_reports_where_the_card_sits_inside_the_artifact(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """#194's margin makes the card an inner rectangle, and the client must not
    guess it: the frame is derived from the artifact's own stored record, so it
    is right for whatever version produced that artifact."""
    image_id = an_image(storage)

    body = client.get(f"/internal/annotation/images/{image_id}").json()

    frame = body["card_frame"]
    assert frame["x"] == pytest.approx(24 / 804)
    assert frame["y"] == pytest.approx(24 / 1104)
    assert frame["width"] == pytest.approx(756 / 804)
    assert frame["height"] == pytest.approx(1056 / 1104)


def test_a_marginless_artifact_reports_the_whole_square_as_the_card(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """An artifact stored before #194 has no margin keys in its record, and its
    card really does reach the artifact's edges — the frame says so rather than
    the record being migrated."""
    image_id = an_image(storage)
    execute(
        sa.update(training_images)
        .where(training_images.c.id == image_id)
        .values(normalization_details={"width": 756, "height": 1056}),
    )

    body = client.get(f"/internal/annotation/images/{image_id}").json()

    assert body["card_frame"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def test_an_image_with_no_artifact_has_no_card_frame(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage, artifact=False)

    body = client.get(f"/internal/annotation/images/{image_id}").json()

    assert body["card_frame"] is None


def test_an_unknown_image_is_a_404(client: TestClient) -> None:
    response = client.get(f"{WORK_LIST}/{uuid.uuid4()}")

    assert response.status_code == 404


def test_a_malformed_identifier_is_a_422(client: TestClient) -> None:
    assert client.get(f"{WORK_LIST}/not-a-uuid").status_code == 422


# ---------------------------------------------------------------------------
# The bytes
# ---------------------------------------------------------------------------


def test_the_artifact_is_served_as_a_png(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    response = client.get(f"{WORK_LIST}/{image_id}/bytes")

    assert response.status_code == 200
    assert response.content == ARTIFACT_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["Cache-Control"] == "private, no-store"


def test_the_original_is_served_as_the_type_it_was_validated_as(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    response = client.get(f"{WORK_LIST}/{image_id}/bytes", params={"representation": "original"})

    assert response.content == PHOTOGRAPH_BYTES
    assert response.headers["content-type"] == "image/jpeg"


def test_asking_for_an_artifact_that_was_never_stored_is_a_404(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Deliberately not the photograph.

    The caller has already been told which representation exists. Substituting
    silently would hand #160 a frame whose coordinates mean nothing — which is
    the one failure this whole surface exists to prevent.
    """
    image_id = an_image(storage, artifact=False)

    assert client.get(f"{WORK_LIST}/{image_id}/bytes").status_code == 404
    assert (
        client.get(
            f"{WORK_LIST}/{image_id}/bytes", params={"representation": "original"}
        ).status_code
        == 200
    )


def test_an_unknown_representation_is_a_422(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    assert (
        client.get(
            f"{WORK_LIST}/{image_id}/bytes", params={"representation": "thumbnail"}
        ).status_code
        == 422
    )


def test_an_unreachable_object_store_is_a_503_naming_it(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    class Unreachable:
        async def get(self, key: StorageKey) -> bytes:
            raise StorageError("the store did not answer")

    client.app.dependency_overrides[object_storage] = Unreachable

    response = client.get(f"{WORK_LIST}/{image_id}/bytes")

    assert response.status_code == 503
    assert response.json()["code"] == "provider_error"
    assert response.json()["details"]["reason"] == "image_store_unreachable"


def test_a_row_naming_bytes_the_store_does_not_hold_is_a_500(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Not a 503: a retry will never fix two stores disagreeing."""
    image_id = an_image(storage)

    class Empty:
        async def get(self, key: StorageKey) -> bytes:
            raise ObjectNotFound(str(key))

    client.app.dependency_overrides[object_storage] = Empty

    response = client.get(f"{WORK_LIST}/{image_id}/bytes")

    assert response.status_code == 500
    assert response.json()["details"]["reason"] == "stored_object_missing"


def test_the_missing_object_is_logged_with_the_image_it_belongs_to(
    client: TestClient, storage: InMemoryObjectStorage, capsys: pytest.CaptureFixture[str]
) -> None:
    """The identifier has to actually reach the log line, not merely be passed to it.

    A stdlib `extra={...}` is silently dropped here: `configure_logging`'s
    `ProcessorFormatter` chain carries no `ExtraAdder`, so the record's attributes
    never reach the rendered event. The line still appeared, still said
    `stored_object_missing`, and named no image — which is worse than not logging
    at all, because it looks like a diagnostic. CodeQL found the call; this is what
    keeps the fix.
    """
    from tcg_api.config import Settings
    from tcg_api.logging import configure_logging

    configure_logging(Settings(log_format="json"))
    image_id = an_image(storage)

    class Empty:
        async def get(self, key: StorageKey) -> bytes:
            raise ObjectNotFound(str(key))

    client.app.dependency_overrides[object_storage] = Empty
    client.get(f"{WORK_LIST}/{image_id}/bytes")

    written = capsys.readouterr()
    line = next(
        entry
        for entry in (written.out + written.err).splitlines()
        if "annotation.stored_object_missing" in entry
    )
    assert str(image_id) in line


# ---------------------------------------------------------------------------
# Recording an annotation — #160
# ---------------------------------------------------------------------------
#: The default of `TCG_API_ANNOTATOR_ID`. Spelled out rather than imported from
#: `Settings`: the point of every assertion below is that the *service* supplied
#: it, and reading it from the same object the service reads would assert nothing.
ANNOTATOR = "annotator"


def annotations_url(image_id: uuid.UUID) -> str:
    return f"{WORK_LIST}/{image_id}/annotations"


def a_corner(**overrides: Any) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "kind": "corner",
        "region": "top_left",
        "label": "whitening",
        "severity": "minor",
        "confidence": 0.8,
        "bbox": {"x": 0.01, "y": 0.02, "width": 0.06, "height": 0.05},
    }
    marker.update(overrides)
    return marker


def a_surface(**overrides: Any) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "kind": "surface",
        "label": "scratch",
        "severity": "minor",
        "confidence": 0.4,
        "representation": "original",
        "bbox": {"x": 0.31, "y": 0.42, "width": 0.02, "height": 0.01},
    }
    marker.update(overrides)
    return marker


# ---------------------------------------------------------------------------
# The four types round-trip
# ---------------------------------------------------------------------------


def test_a_corner_marker_is_stored_where_it_was_placed(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    created = client.post(annotations_url(image_id), json={"markers": [a_corner()]})
    assert created.status_code == 201

    read_back = client.get(f"{WORK_LIST}/{image_id}").json()["annotations"]
    assert len(read_back) == 1
    marker = read_back[0]
    assert marker["kind"] == "corner"
    assert marker["region"] == "top_left"
    assert marker["label"] == "whitening"
    assert marker["severity"] == "minor"
    # The coordinates come back as the fractions they went in as. This is the
    # assertion the whole viewer exists to make true.
    assert marker["bbox"] == {"x": 0.01, "y": 0.02, "width": 0.06, "height": 0.05}
    # The request has no representation field for a corner; the service wrote
    # the only frame a corner can be in (#175).
    assert marker["representation"] == "normalized"


def test_an_edge_marker_takes_an_edge_label_a_corner_cannot(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    created = client.post(
        annotations_url(image_id),
        json={
            "markers": [
                {
                    "kind": "edge",
                    "region": "left",
                    "label": "rough_cut",
                    "severity": "moderate",
                    "confidence": 0.6,
                }
            ]
        },
    )
    assert created.status_code == 201

    # …and the same label on a corner is refused, which is why §14 and §15 are
    # two lists rather than one.
    refused = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(label="rough_cut", bbox=None)]},
    )
    assert refused.status_code == 422


def test_a_surface_marker_names_no_region_and_is_placed_by_its_box(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    created = client.post(
        annotations_url(image_id),
        json={
            "markers": [
                {
                    "kind": "surface",
                    "label": "scratch",
                    "severity": "minor",
                    "confidence": 0.4,
                    "representation": "normalized",
                    "bbox": {"x": 0.3, "y": 0.4, "width": 0.1, "height": 0.02},
                }
            ]
        },
    )
    assert created.status_code == 201
    assert created.json()["markers"][0]["region"] is None

    # §16 names no positions, so a surface annotation that claims one is refused.
    refused = client.post(
        annotations_url(image_id),
        json={
            "markers": [
                {
                    "kind": "surface",
                    "region": "top_left",
                    "label": "scratch",
                    "severity": "minor",
                    "confidence": 0.4,
                    "representation": "normalized",
                }
            ]
        },
    )
    assert refused.status_code == 422


def test_a_centering_measurement_is_stored_as_two_ratios(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    created = client.post(
        annotations_url(image_id),
        json={
            "centering": {
                "horizontal": 0.55,
                "vertical": 0.49,
                "confidence": 0.9,
                "notes": "conventional border",
            }
        },
    )
    assert created.status_code == 201

    stored = client.get(f"{WORK_LIST}/{image_id}").json()["centering"]
    assert len(stored) == 1
    assert stored[0]["horizontal"] == 0.55
    assert stored[0]["vertical"] == 0.49
    assert stored[0]["notes"] == "conventional border"


def test_an_axis_with_no_border_is_null_and_never_a_half(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """§21 names full-art and borderless layouts, and 0.5 is not the answer for one."""
    image_id = an_image(storage)

    created = client.post(
        annotations_url(image_id),
        json={"centering": {"horizontal": 0.51, "vertical": None, "confidence": 0.7}},
    )
    assert created.status_code == 201
    assert created.json()["centering"][0]["vertical"] is None

    measuring_neither = client.post(
        annotations_url(image_id),
        json={"centering": {"horizontal": None, "vertical": None, "confidence": 0.7}},
    )
    assert measuring_neither.status_code == 422


# ---------------------------------------------------------------------------
# Uncertainty — §30's eleventh feature, on both tables
# ---------------------------------------------------------------------------


def test_an_annotation_can_be_saved_as_i_cannot_tell(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """`unknown` carries no severity, which is what makes admitting it one action."""
    image_id = an_image(storage)

    created = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(label="unknown", severity=None, confidence=0.2)]},
    )
    assert created.status_code == 201

    marker = created.json()["markers"][0]
    assert marker["label"] == "unknown"
    assert marker["severity"] is None
    assert marker["confidence"] == 0.2


def test_a_defect_marker_without_a_severity_cannot_be_saved(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    refused = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(severity=None)]},
    )
    assert refused.status_code == 422


def test_a_clean_corner_carrying_a_severity_is_refused_too(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """The rule is an equality between two facts, not one implication.

    A sound corner has nothing to rate, so `clean` with a severity is as wrong as
    `chipping` without one — `ck_image_annotations_a_defect_carries_a_severity`
    is written that way and this is the half a lax mirror would let through.
    """
    image_id = an_image(storage)

    refused = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(label="clean", severity="minor")]},
    )
    assert refused.status_code == 422

    accepted = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(label="clean", severity=None)]},
    )
    assert accepted.status_code == 201


def test_a_confidence_is_required_and_never_defaulted(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """The column is NOT NULL with no server default, on purpose.

    A default would read as certainty for every row nobody supplied one for.
    """
    image_id = an_image(storage)
    marker = a_corner()
    del marker["confidence"]

    refused = client.post(annotations_url(image_id), json={"markers": [marker]})
    assert refused.status_code == 422


# ---------------------------------------------------------------------------
# The artifact gate
# ---------------------------------------------------------------------------


def test_a_bounding_box_needs_an_artifact_to_be_a_fraction_of(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage, artifact=False)

    refused = client.post(annotations_url(image_id), json={"markers": [a_corner()]})
    assert refused.status_code == 409


def test_a_centering_measurement_needs_an_artifact_too(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """A ratio is read off where the borders sit in the artifact, so it is a coordinate."""
    image_id = an_image(storage, artifact=False)

    refused = client.post(
        annotations_url(image_id),
        json={"centering": {"horizontal": 0.5, "vertical": 0.5, "confidence": 0.9}},
    )
    assert refused.status_code == 409


def test_a_surface_marker_against_the_original_needs_no_artifact(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """#175, end to end: the row the artifact could not honestly hold.

    ADR 0010 measured §16's fine classes below the artifact's sampling limit
    and named the original photograph the one route back — and the original
    always exists, so the gate has nothing to refuse.
    """
    image_id = an_image(storage, artifact=False)

    created = client.post(annotations_url(image_id), json={"markers": [a_surface()]})
    assert created.status_code == 201

    (marker,) = client.get(f"{WORK_LIST}/{image_id}").json()["annotations"]
    assert marker["representation"] == "original"
    assert marker["bbox"] == {"x": 0.31, "y": 0.42, "width": 0.02, "height": 0.01}


def test_a_surface_marker_declaring_normalized_still_needs_the_artifact(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Declaring 'normalized' is a claim about the artifact, box or no box.

    With a box the fractions would index into nothing; without one the claim
    still names a frame that does not exist, and the client can always declare
    'original' there instead — so neither strands the image.
    """
    image_id = an_image(storage, artifact=False)

    boxed = client.post(
        annotations_url(image_id),
        json={"markers": [a_surface(representation="normalized")]},
    )
    assert boxed.status_code == 409

    boxless = client.post(
        annotations_url(image_id),
        json={"markers": [a_surface(representation="normalized", bbox=None)]},
    )
    assert boxless.status_code == 409


def test_a_surface_marker_without_a_declaration_is_refused(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Required with no default — a frame nobody named is not 'normalized'."""
    image_id = an_image(storage)
    marker = a_surface()
    del marker["representation"]

    refused = client.post(annotations_url(image_id), json={"markers": [marker]})
    assert refused.status_code == 422


def test_a_corner_naming_a_representation_is_refused_not_dropped(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """A corner is always in the artifact's frame, and `extra="forbid"` says so.

    Accepting and discarding the field would leave a client believing it had
    marked the original photograph.
    """
    image_id = an_image(storage)

    refused = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(representation="original")]},
    )
    assert refused.status_code == 422


def test_a_marker_with_no_box_is_still_recordable_without_an_artifact(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """A corner's region names its position, so this says something true.

    Refusing the whole request would also strand such an image at the head of the
    work list for ever, which is a worse answer than a partial one.
    """
    image_id = an_image(storage, artifact=False)

    created = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner(bbox=None)]},
    )
    assert created.status_code == 201


def test_a_box_outside_the_artifact_is_refused(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)

    for bbox in (
        {"x": 0.98, "y": 0.1, "width": 0.1, "height": 0.1},  # runs off the right
        {"x": 0.1, "y": 0.1, "width": 0.0, "height": 0.1},  # no area
        {"x": -0.1, "y": 0.1, "width": 0.1, "height": 0.1},  # before the left edge
    ):
        refused = client.post(annotations_url(image_id), json={"markers": [a_corner(bbox=bbox)]})
        assert refused.status_code == 422, bbox


# ---------------------------------------------------------------------------
# What the service supplies, and what it refuses to be told
# ---------------------------------------------------------------------------


def test_the_annotator_and_the_timestamp_are_recorded_without_being_sent(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """§30 asks for both automatically. The request below contains neither."""
    image_id = an_image(storage)

    created = client.post(annotations_url(image_id), json={"markers": [a_corner()]})
    assert created.status_code == 201

    marker = created.json()["markers"][0]
    assert marker["annotator_id"] == ANNOTATOR
    assert marker["created_at"]


def test_a_client_cannot_name_the_annotator(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Refused, not ignored — which is what keeps `annotator_id`'s grammar out of reach.

    Dropping the field silently would leave a client believing it had set the
    annotator, and spec §53's restraint is exactly the kind of thing nobody should
    be able to believe they have circumvented.
    """
    image_id = an_image(storage)

    refused = client.post(
        annotations_url(image_id),
        json={"markers": [a_corner()], "annotator_id": "someone@example.com"},
    )
    assert refused.status_code == 422


# ---------------------------------------------------------------------------
# The rest of the contract
# ---------------------------------------------------------------------------


def test_an_annotation_recording_nothing_is_refused(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """It would take the image off the work list having said nothing."""
    image_id = an_image(storage)

    refused = client.post(annotations_url(image_id), json={"markers": [], "centering": None})
    assert refused.status_code == 422


def test_a_saved_image_leaves_the_work_list(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    image_id = an_image(storage)
    assert client.get(WORK_LIST).json()["total"] == 1

    client.post(annotations_url(image_id), json={"markers": [a_corner()]})

    assert client.get(WORK_LIST).json()["total"] == 0


def test_a_refused_save_leaves_nothing_behind(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """A refused marker must not leave the measurement that travelled with it.

    Refused here by the pydantic mirror, before a statement runs. The other half
    — a marker PostgreSQL refuses after the measurement has been inserted — is
    held by `record_annotations` not committing and the handler committing once,
    which is why the two inserts are in one function rather than two calls.
    """
    image_id = an_image(storage)

    refused = client.post(
        annotations_url(image_id),
        json={
            "markers": [a_corner(label="rough_cut")],
            "centering": {"horizontal": 0.5, "vertical": 0.5, "confidence": 0.9},
        },
    )
    assert refused.status_code == 422

    detail = client.get(f"{WORK_LIST}/{image_id}").json()
    assert detail["annotations"] == []
    assert detail["centering"] == []
    assert client.get(WORK_LIST).json()["total"] == 1


def test_a_correction_is_a_new_row_and_both_survive(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Append-only: the newest row for a region is the current view of it."""
    image_id = an_image(storage)

    client.post(annotations_url(image_id), json={"markers": [a_corner(severity="minor")]})
    client.post(annotations_url(image_id), json={"markers": [a_corner(severity="severe")]})

    stored = client.get(f"{WORK_LIST}/{image_id}").json()["annotations"]
    assert [marker["severity"] for marker in stored] == ["minor", "severe"]


def test_saving_against_an_unknown_image_is_a_404(client: TestClient) -> None:
    refused = client.post(annotations_url(uuid.uuid4()), json={"markers": [a_corner()]})
    assert refused.status_code == 404


def test_saving_against_a_malformed_identifier_is_a_422(client: TestClient) -> None:
    refused = client.post(f"{WORK_LIST}/not-a-uuid/annotations", json={"markers": [a_corner()]})
    assert refused.status_code == 422


def test_an_unannotated_image_reports_two_empty_lists(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Not an omission and not an error — the same fact the work list reports."""
    image_id = an_image(storage)

    detail = client.get(f"{WORK_LIST}/{image_id}").json()
    assert detail["annotations"] == []
    assert detail["centering"] == []
