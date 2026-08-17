"""One contract, run against every adapter.

The acceptance criterion for #17 is that "swapping the adapter requires no
change to calling code". A second implementation asserted to be equivalent is
worth very little; a single suite that both implementations must satisfy is the
evidence itself, so these tests are parametrised over the adapters rather than
duplicated per adapter. Adding a third store means adding a fixture parameter
and nothing else — and if it cannot pass, it is not an `ObjectStorage`.

The in-memory params always run. The S3 params carry the `object_storage`
marker and skip unless `TCG_API_STORAGE_ENDPOINT_URL` points at a live MinIO,
so the default suite stays hermetic:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait

Three tests near the bottom run against MinIO *only*, deliberately. They check
that a signature is honoured and then refused, and no in-memory store can be
honest about that: there is no server to enforce it.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tcg_shared.storage import (
    InMemoryObjectStorage,
    ObjectNotFound,
    ObjectStorage,
    generate_key,
)
from tcg_shared.storage.s3 import S3ObjectStorage, create_s3_client

ENDPOINT_URL = os.environ.get("TCG_API_STORAGE_ENDPOINT_URL")
BUCKET = os.environ.get("TCG_API_STORAGE_BUCKET", "tcg-local")
REGION = os.environ.get("TCG_API_STORAGE_REGION", "us-east-1")
ACCESS_KEY_ID = os.environ.get("TCG_API_STORAGE_ACCESS_KEY_ID", "tcg")
SECRET_ACCESS_KEY = os.environ.get("TCG_API_STORAGE_SECRET_ACCESS_KEY", "tcglocaldev")

JPEG = "image/jpeg"

#: Short enough that the expiry test finishes quickly, long enough to survive
#: the clock skew between the host and the container MinIO runs in — a 1-second
#: grant is already spent by the time the first request arrives.
SHORT_TTL = timedelta(seconds=5)

needs_minio = pytest.mark.skipif(
    not ENDPOINT_URL,
    reason="TCG_API_STORAGE_ENDPOINT_URL is unset; no live MinIO to exercise",
)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    """Drive one async scenario.

    `asyncio.run` inside a sync test, matching `services/api/tests/test_database.py`:
    this repository has no pytest-asyncio and adding one for five tests would be
    a dependency in exchange for a decorator.
    """
    return asyncio.run(scenario())


def _s3_storage() -> S3ObjectStorage:
    return S3ObjectStorage(
        client=create_s3_client(
            endpoint_url=ENDPOINT_URL,
            region=REGION,
            access_key_id=ACCESS_KEY_ID,
            secret_access_key=SECRET_ACCESS_KEY,
        ),
        bucket=BUCKET,
    )


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("s3", id="s3", marks=[pytest.mark.object_storage, needs_minio]),
    ]
)
def storage(request: pytest.FixtureRequest) -> ObjectStorage:
    """Each adapter in turn. Every test below holds for all of them."""
    if request.param == "memory":
        return InMemoryObjectStorage()
    return _s3_storage()


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_an_object_survives_a_round_trip(storage: ObjectStorage) -> None:
    key = generate_key("contract")

    async def scenario() -> bytes:
        await storage.put(key, b"card-front-bytes", content_type=JPEG)
        return await storage.get(key)

    assert run(scenario) == b"card-front-bytes"


def test_putting_twice_replaces_the_object(storage: ObjectStorage) -> None:
    key = generate_key("contract")

    async def scenario() -> bytes:
        await storage.put(key, b"first", content_type=JPEG)
        await storage.put(key, b"second", content_type=JPEG)
        return await storage.get(key)

    assert run(scenario) == b"second"


def test_getting_an_absent_key_raises_object_not_found(storage: ObjectStorage) -> None:
    key = generate_key("contract")

    async def scenario() -> bytes:
        return await storage.get(key)

    with pytest.raises(ObjectNotFound):
        run(scenario)


def test_deleting_removes_the_object(storage: ObjectStorage) -> None:
    key = generate_key("contract")

    async def scenario() -> None:
        await storage.put(key, b"transient", content_type=JPEG)
        await storage.delete(key)
        await storage.get(key)

    with pytest.raises(ObjectNotFound):
        run(scenario)


def test_deleting_an_absent_key_succeeds(storage: ObjectStorage) -> None:
    """Retention (spec §54) wants the object gone; it already is."""
    key = generate_key("contract")

    async def scenario() -> None:
        await storage.delete(key)

    run(scenario)


def test_objects_under_different_keys_do_not_collide(storage: ObjectStorage) -> None:
    first, second = generate_key("contract"), generate_key("contract")

    async def scenario() -> tuple[bytes, bytes]:
        await storage.put(first, b"one", content_type=JPEG)
        await storage.put(second, b"two", content_type=JPEG)
        return await storage.get(first), await storage.get(second)

    assert run(scenario) == (b"one", b"two")


@pytest.mark.parametrize("operation", ["signed_upload_url", "signed_download_url"])
def test_a_signed_url_records_a_timezone_aware_expiry(
    storage: ObjectStorage, operation: str
) -> None:
    key = generate_key("contract")
    ttl = timedelta(minutes=15)

    async def scenario() -> datetime:
        if operation == "signed_upload_url":
            signed = await storage.signed_upload_url(key, content_type=JPEG, expires_in=ttl)
        else:
            signed = await storage.signed_download_url(key, expires_in=ttl)
        return signed.expires_at

    expires_at = run(scenario)

    assert expires_at.tzinfo is not None, "a naive expiry is unreadable in another timezone"
    assert abs((expires_at - (datetime.now(UTC) + ttl)).total_seconds()) < 10


@pytest.mark.parametrize("operation", ["signed_upload_url", "signed_download_url"])
def test_a_signed_url_is_scoped_to_the_one_key_it_was_minted_for(
    storage: ObjectStorage, operation: str
) -> None:
    """Spec §55: possession of one URL must never imply access to another object."""
    mine, someone_elses = generate_key("contract"), generate_key("contract")
    ttl = timedelta(minutes=15)

    async def scenario() -> str:
        if operation == "signed_upload_url":
            signed = await storage.signed_upload_url(mine, content_type=JPEG, expires_in=ttl)
        else:
            signed = await storage.signed_download_url(mine, expires_in=ttl)
        return signed.url

    url = run(scenario)

    assert str(mine) in url
    assert str(someone_elses) not in url


# ---------------------------------------------------------------------------
# Signature enforcement — MinIO only.
#
# An in-memory store can mint a URL but cannot honour or refuse one, so these
# are the tests that only a real S3-compatible implementation can pass.
# ---------------------------------------------------------------------------


@pytest.mark.object_storage
@needs_minio
def test_a_signed_upload_url_accepts_a_body_the_port_can_then_read() -> None:
    storage = _s3_storage()
    key = generate_key("contract")

    async def mint() -> str:
        signed = await storage.signed_upload_url(
            key, content_type=JPEG, expires_in=timedelta(minutes=5)
        )
        return signed.url

    async def read_back() -> bytes:
        return await storage.get(key)

    # The Content-Type must match the one signed for, or the signature the
    # server recomputes will not be the one we sent.
    response = httpx.put(run(mint), content=b"uploaded-directly", headers={"Content-Type": JPEG})
    response.raise_for_status()

    assert run(read_back) == b"uploaded-directly"


@pytest.mark.object_storage
@needs_minio
def test_a_signed_download_url_serves_the_object() -> None:
    storage = _s3_storage()
    key = generate_key("contract")

    async def scenario() -> str:
        await storage.put(key, b"downloaded-directly", content_type=JPEG)
        signed = await storage.signed_download_url(key, expires_in=timedelta(minutes=5))
        return signed.url

    response = httpx.get(run(scenario))

    assert response.content == b"downloaded-directly"


@pytest.mark.object_storage
@needs_minio
def test_a_signed_url_stops_working_once_it_expires() -> None:
    """A URL that outlived its grant is a credential nobody revoked."""
    storage = _s3_storage()
    key = generate_key("contract")

    async def scenario() -> str:
        await storage.put(key, b"briefly-available", content_type=JPEG)
        signed = await storage.signed_download_url(key, expires_in=SHORT_TTL)
        return signed.url

    url = run(scenario)
    assert httpx.get(url).status_code == 200, "the URL should work before it expires"

    time.sleep(SHORT_TTL.total_seconds() + 2)

    assert httpx.get(url).status_code == 403
