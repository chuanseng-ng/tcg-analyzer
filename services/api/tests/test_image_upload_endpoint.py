"""`POST /analyses/{id}/images` — issue #33, spec §11, §54 and §55.

`test_image_validation.py` asserts what the validator does with bytes. This file
asserts what the *endpoint* does with them: whose analysis a photograph may join,
where it is stored, what is written down about it, and where the analysis ends
up afterwards. Those are properties of a `WHERE` clause, a state machine and an
object store, so they are tested against real PostgreSQL rather than a fake —
the same reasoning `test_analyses_endpoint.py` records.

Object storage is the one dependency stubbed, with the in-memory adapter the
contract tests already run the S3 one against. Nothing here needs a signature to
be honoured; what it needs is to see which keys were written and which were
deleted.

Skipped unless `TCG_API_DATABASE_URL` points at a live PostgreSQL:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Final

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from PIL.ExifTags import GPS, IFD, Base
from sqlalchemy.ext.asyncio import create_async_engine
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.errors import ErrorCode
from tcg_api.routers.analyses import SESSION_COOKIE, object_storage
from tcg_api.storage import get_object_storage
from tcg_shared.storage import InMemoryObjectStorage, StorageKey, StorageUnavailable

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL,
    reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
)

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

#: The one message every "there is no such analysis for you" answer carries.
NOT_FOUND: Final = "No analysis is recorded under that identifier."

#: What a server-generated key looks like: a namespace, a date partition and a
#: UUID. Nothing a client sent appears in it, because nothing a client sent is
#: accepted — the endpoint has no filename parameter at all.
KEY_SHAPE: Final = re.compile(
    r"^uploads/\d{4}/\d{2}/\d{2}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    """Bring the schema up and empty the session tree once for the module."""
    if not DATABASE_URL:
        return

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    async def empty() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("TRUNCATE analysis_sessions RESTART IDENTITY CASCADE")
                )
        finally:
            await engine.dispose()

    run(empty)


def querying(statement: str, **parameters: Any) -> Any:
    """Read one value straight out of PostgreSQL, past the API."""

    async def read() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                result = await connection.execute(sa.text(statement), parameters)
                return result.scalar()
        finally:
            await engine.dispose()

    return run(read)


def executing(statement: str, **parameters: Any) -> None:
    async def write() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(statement), parameters)
        finally:
            await engine.dispose()

    run(write)


# ---------------------------------------------------------------------------
# The pictures
# ---------------------------------------------------------------------------
def a_photograph(colour: tuple[int, int, int] = (200, 40, 40), *, located: bool = False) -> bytes:
    """A small JPEG, optionally carrying the GPS fix a phone would attach."""
    picture = Image.new("RGB", (48, 32), colour)
    buffer = BytesIO()
    if not located:
        picture.save(buffer, "JPEG", quality=90)
        return buffer.getvalue()

    exif = Image.Exif()
    exif[Base.Make] = "TestCam"
    gps = exif.get_ifd(IFD.GPSInfo)
    gps[GPS.GPSLatitudeRef] = "N"
    gps[GPS.GPSLatitude] = (1.0, 17.0, 3.0)
    picture.save(buffer, "JPEG", quality=90, exif=exif)
    return buffer.getvalue()


def send(client: TestClient, analysis_id: str, side: str, body: bytes) -> Any:
    return client.post(
        f"/analyses/{analysis_id}/images",
        params={"side": side},
        content=body,
        headers={"Content-Type": "image/jpeg"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def storage() -> InMemoryObjectStorage:
    """The object store, as a dictionary this test can look inside."""
    return InMemoryObjectStorage()


@pytest.fixture
def client(storage: InMemoryObjectStorage) -> Iterator[TestClient]:
    """One anonymous user, with their own cookie jar and their own bucket.

    `with`, and the caches cleared first, for the reasons
    `test_analyses_endpoint.py` records: one event loop per test, and no pool
    left over from the previous one.
    """
    for cached in CACHES:
        cached.cache_clear()
    app = create_app()
    app.dependency_overrides[object_storage] = lambda: storage
    with TestClient(app) as instance:
        yield instance


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Stand in for the broker, so a run can be asked for without one."""
    handed: list[uuid.UUID] = []

    def record(analysis_id: uuid.UUID) -> str:
        handed.append(analysis_id)
        return "job-1"

    monkeypatch.setattr("tcg_api.routers.analyses.enqueue_analysis", record)
    return handed


# ---------------------------------------------------------------------------
# The acceptance criterion: front and back upload, validate and persist
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_photograph_uploads_and_is_recorded(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    created = client.post("/analyses").json()

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 201
    body = response.json()
    assert body["side"] == "front"
    assert body["mime_type"] == "image/jpeg"
    assert re.fullmatch(r"[0-9a-f]{64}", body["sha256"])
    assert body["analysis_id"] == created["id"]
    assert len(storage.objects) == 1


@pytest.mark.integration
@requires_postgres
def test_both_sides_take_the_analysis_to_uploaded(client: TestClient) -> None:
    """The state machine's first two steps, and the reason #35 left them unreached.

    Two transitions, not one: spec §65's graph has no shortcut from `created` to
    `uploaded`, and the analysis is genuinely in a different state after one
    photograph than after two.
    """
    created = client.post("/analyses").json()

    first = send(client, created["id"], "front", a_photograph())
    second = send(client, created["id"], "back", a_photograph((30, 90, 200)))

    assert first.json()["analysis_status"] == "uploading"
    assert second.json()["analysis_status"] == "uploaded"
    assert client.get(f"/analyses/{created['id']}").json()["status"] == "uploaded"


@pytest.mark.integration
@requires_postgres
def test_an_analysis_with_both_sides_can_be_run(
    client: TestClient, enqueued: list[uuid.UUID]
) -> None:
    """The join #33 exists to make: uploading is what makes `run` reachable.

    Until this endpoint landed, `uploaded` was reached only by a test writing
    the row directly — which is to say the product had no way in.
    """
    created = client.post("/analyses").json()
    send(client, created["id"], "front", a_photograph())
    send(client, created["id"], "back", a_photograph((30, 90, 200)))

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 202
    assert enqueued == [uuid.UUID(created["id"])]


@pytest.mark.integration
@requires_postgres
def test_one_side_is_not_enough_to_run(client: TestClient, enqueued: list[uuid.UUID]) -> None:
    """Spec §18's pipeline begins with images — both of them."""
    created = client.post("/analyses").json()
    send(client, created["id"], "front", a_photograph())

    response = client.post(f"/analyses/{created['id']}/run")

    assert response.status_code == 409
    assert enqueued == []


# ---------------------------------------------------------------------------
# Server-generated storage paths — spec §55
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_the_storage_key_is_generated_by_the_server(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    created = client.post("/analyses").json()
    send(client, created["id"], "front", a_photograph())

    key = next(iter(storage.objects))
    assert KEY_SHAPE.fullmatch(key.value), key.value
    assert (
        querying(
            "SELECT original_uri FROM images WHERE analysis_id = :id", id=uuid.UUID(created["id"])
        )
        == key.value
    )


@pytest.mark.integration
@requires_postgres
def test_nothing_the_client_sends_can_influence_the_key(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """There is no filename parameter, so there is nothing to sanitise.

    Spec §55 asks for filenames to be sanitised and storage paths generated
    server-side. Taking the image as the request body satisfies both by
    construction: the only things a caller controls are the bytes, the declared
    type and the side, and none of the three reaches `generate_key`.
    """
    created = client.post("/analyses").json()

    response = client.post(
        f"/analyses/{created['id']}/images",
        params={"side": "front"},
        content=a_photograph(),
        headers={
            "Content-Type": "image/jpeg",
            "Content-Disposition": 'attachment; filename="../../../etc/passwd"',
            "X-Filename": "../../../etc/passwd",
        },
    )

    assert response.status_code == 201
    key = next(iter(storage.objects))
    assert KEY_SHAPE.fullmatch(key.value), key.value
    assert "passwd" not in key.value


@pytest.mark.integration
@requires_postgres
def test_a_traversal_in_the_side_is_a_validation_error(client: TestClient) -> None:
    created = client.post("/analyses").json()

    response = send(client, created["id"], "../../etc/passwd", a_photograph())

    assert response.status_code == 422


@pytest.mark.integration
@requires_postgres
def test_a_side_the_v1_pipeline_cannot_process_is_refused(client: TestClient) -> None:
    """`images.side` admits all six of spec §11's values; the endpoint takes two.

    The other four are §52's guided photography. Accepting one would store an
    image no stage in this milestone knows what to do with.
    """
    created = client.post("/analyses").json()

    response = send(client, created["id"], "surface_front", a_photograph())

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# What is stored, and what is not — spec §54
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_the_stored_bytes_are_the_stripped_bytes(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """The privacy claim, end to end rather than in the validator alone."""
    located = a_photograph(located=True)
    assert dict(Image.open(BytesIO(located)).getexif()), "the fixture must carry EXIF"
    created = client.post("/analyses").json()

    body = send(client, created["id"], "front", located).json()

    stored = next(iter(storage.objects.values())).data
    assert stored != located
    assert body["sha256"] == sha256(stored).hexdigest()
    with Image.open(BytesIO(stored)) as kept:
        assert dict(kept.getexif()) == {}
        assert dict(kept.getexif().get_ifd(IFD.GPSInfo)) == {}


@pytest.mark.integration
@requires_postgres
def test_the_recorded_digest_is_of_the_stored_bytes(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """#39's cache key must name bytes that exist; #41 must be able to check them."""
    created = client.post("/analyses").json()

    body = send(client, created["id"], "front", a_photograph(located=True)).json()

    stored = next(iter(storage.objects.values())).data
    assert body["sha256"] == sha256(stored).hexdigest()
    assert (
        querying("SELECT sha256 FROM images WHERE analysis_id = :id", id=uuid.UUID(created["id"]))
        == body["sha256"]
    )


@pytest.mark.integration
@requires_postgres
def test_the_derived_columns_are_left_for_later_stages(client: TestClient) -> None:
    """The upload knows what arrived; it does not know what it is worth."""
    created = client.post("/analyses").json()
    send(client, created["id"], "front", a_photograph())

    unset = querying(
        "SELECT count(*) FROM images WHERE analysis_id = :id AND normalized_uri IS NULL "
        "AND width IS NULL AND height IS NULL AND quality_score IS NULL "
        "AND quality_status IS NULL",
        id=uuid.UUID(created["id"]),
    )
    assert unset == 1


# ---------------------------------------------------------------------------
# A retake is a replacement — #31's UNIQUE (analysis_id, side)
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_retake_replaces_the_photograph(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    created = client.post("/analyses").json()
    first = send(client, created["id"], "front", a_photograph()).json()

    second = send(client, created["id"], "front", a_photograph((10, 200, 90))).json()

    assert first["sha256"] != second["sha256"]
    assert (
        querying(
            "SELECT count(*) FROM images WHERE analysis_id = :id AND side = 'front'",
            id=uuid.UUID(created["id"]),
        )
        == 1
    )


@pytest.mark.integration
@requires_postgres
def test_a_retake_deletes_the_photograph_it_replaced(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """Cascading a row away does not delete an object — spec §54, and #41's problem.

    An object no row points at is one a retention sweep working from rows will
    never find, so the superseded key is deleted here rather than left behind.
    """
    created = client.post("/analyses").json()
    send(client, created["id"], "front", a_photograph())
    superseded = next(iter(storage.objects))

    send(client, created["id"], "front", a_photograph((10, 200, 90)))

    assert superseded not in storage.objects
    assert len(storage.objects) == 1


@pytest.mark.integration
@requires_postgres
def test_a_retake_after_both_sides_leaves_the_analysis_uploaded(client: TestClient) -> None:
    """It is still true that every side has arrived."""
    created = client.post("/analyses").json()
    send(client, created["id"], "front", a_photograph())
    send(client, created["id"], "back", a_photograph((30, 90, 200)))

    retaken = send(client, created["id"], "front", a_photograph((10, 200, 90)))

    assert retaken.json()["analysis_status"] == "uploaded"


@pytest.mark.integration
@requires_postgres
def test_an_analysis_in_flight_refuses_a_photograph(client: TestClient) -> None:
    """Changing the inputs under a running job is not a retake."""
    created = client.post("/analyses").json()
    executing(
        "UPDATE analyses SET status = 'analyzing' WHERE id = :id",
        id=uuid.UUID(created["id"]),
    )

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Rejections — every one of them `invalid_image`, spec §66
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_a_non_image_is_refused_and_stores_nothing(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    created = client.post("/analyses").json()

    response = send(client, created["id"], "front", b"#!/bin/sh\nrm -rf /\n")

    assert response.status_code == 400
    assert response.json()["code"] == ErrorCode.INVALID_IMAGE
    assert storage.objects == {}
    assert (
        querying("SELECT count(*) FROM images WHERE analysis_id = :id", id=uuid.UUID(created["id"]))
        == 0
    )


@pytest.mark.integration
@requires_postgres
def test_a_rejection_leaks_no_internal_detail(client: TestClient) -> None:
    """The acceptance criterion, literally: the message is the rule, not the trace."""
    created = client.post("/analyses").json()

    body = send(client, created["id"], "front", b"not an image at all").json()

    assert body["message"] == "The upload is not a JPEG or PNG image."
    assert body["details"] is None


@pytest.mark.integration
@requires_postgres
def test_an_oversized_upload_is_refused(
    client: TestClient, storage: InMemoryObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused while it is arriving, not after it has all been buffered."""
    monkeypatch.setenv("TCG_API_UPLOAD_MAX_BYTES", "512")
    get_settings.cache_clear()
    created = client.post("/analyses").json()

    response = send(client, created["id"], "front", a_photograph() + b"\x00" * 2048)

    assert response.status_code == 400
    assert response.json()["code"] == ErrorCode.INVALID_IMAGE
    assert "larger than" in response.json()["message"]
    assert storage.objects == {}


@pytest.mark.integration
@requires_postgres
def test_an_upload_past_the_pixel_limit_is_refused(
    client: TestClient, storage: InMemoryObjectStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TCG_API_UPLOAD_MAX_PIXELS", "16")
    get_settings.cache_clear()
    created = client.post("/analyses").json()

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 400
    assert "pixels" in response.json()["message"]
    assert storage.objects == {}


# ---------------------------------------------------------------------------
# Whose analysis it is — one 404, four ways of not having one
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_another_sessions_analysis_cannot_be_uploaded_to(
    client: TestClient, storage: InMemoryObjectStorage
) -> None:
    """The property that matters most: one user's photographs are their own."""
    created = client.post("/analyses").json()
    client.cookies.clear()
    client.post("/analyses")

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND
    assert storage.objects == {}


@pytest.mark.integration
@requires_postgres
def test_an_unknown_analysis_answers_exactly_as_a_forbidden_one(client: TestClient) -> None:
    created = client.post("/analyses").json()
    client.cookies.clear()
    client.post("/analyses")

    forbidden = send(client, created["id"], "front", a_photograph())
    unknown = send(client, str(uuid.uuid4()), "front", a_photograph())

    assert forbidden.status_code == unknown.status_code == 404
    assert forbidden.json() == unknown.json()


@pytest.mark.integration
@requires_postgres
def test_an_upload_without_a_session_is_the_same_404(client: TestClient) -> None:
    created = client.post("/analyses").json()
    client.cookies.clear()

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_an_expired_session_is_the_same_404(client: TestClient) -> None:
    created = client.post("/analyses").json()
    executing(
        "UPDATE analysis_sessions "
        "SET created_at = now() - interval '2 days', expires_at = now() - interval '1 day' "
        "WHERE anonymous_session_id = :token",
        token=client.cookies[SESSION_COOKIE],
    )

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 404
    assert response.json()["detail"] == NOT_FOUND


@pytest.mark.integration
@requires_postgres
def test_a_malformed_identifier_is_a_validation_error(client: TestClient) -> None:
    client.post("/analyses")

    response = send(client, "not-a-uuid", "front", a_photograph())

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# An unreachable image store
# ---------------------------------------------------------------------------
@pytest.mark.integration
@requires_postgres
def test_an_unreachable_image_store_is_a_503(client: TestClient) -> None:
    """A different `details.reason` from the analysis store's and the queue's.

    Three dependencies can be down, and an operator reading a 503 should be told
    which rather than made to guess.
    """

    class Refusing(InMemoryObjectStorage):
        async def put(self, key: StorageKey, data: bytes, *, content_type: str) -> None:
            raise StorageUnavailable("no route to the bucket")

    client.app.dependency_overrides[object_storage] = Refusing  # type: ignore[attr-defined]
    created = client.post("/analyses").json()

    response = send(client, created["id"], "front", a_photograph())

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == ErrorCode.PROVIDER_ERROR
    assert body["details"] == {"reason": "image_store_unreachable"}
    assert (
        querying("SELECT count(*) FROM images WHERE analysis_id = :id", id=uuid.UUID(created["id"]))
        == 0
    )
