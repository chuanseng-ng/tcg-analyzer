"""The internal annotation surface — #159.

Integration rather than stubbed, and deliberately: the thing under test *is* the
predicate. "Awaiting annotation" is `NOT EXISTS` against two child tables, and a
stub repository would assert the stub. `test_analyses_endpoint.py`'s pattern —
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
            "normalization_details": {"width": 756, "height": 1056} if artifact else None,
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
