"""ADR 0008's approved class 4, end to end — #148.

Against real PostgreSQL **and** a real object store, because the whole claim is
about bytes: a consented photograph survives its session's expiry, and an
unconsented one does not. A stub answering that in Python would be testing the
stub, and the two facts that matter — the `training_images` row and the object
under `training/` — live in the two services this module needs.

The acceptance criterion is the first test in this file: a user consents, their
session is swept, their photographs are still there, and the code they were
shown takes them back.

Skipped unless both are set:

    docker compose -f infrastructure/local/docker-compose.yml up -d --wait postgres minio
    export TCG_API_DATABASE_URL=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg
    export TCG_API_STORAGE_ENDPOINT_URL=http://localhost:9000

It carries both markers and both skips, like `test_anonymous_journey.py`: CI's
database job has no MinIO and its storage job has both.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tcg_api.analysis.retention import purge_expired
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.datasets.consent import ACQUISITION_METHOD, CONSENT_VERSION, SOURCE
from tcg_api.storage import create_object_storage, get_object_storage
from tcg_shared.storage import StorageError, StorageKey

REPO_ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = os.environ.get("TCG_API_DATABASE_URL")
ENDPOINT_URL = os.environ.get("TCG_API_STORAGE_ENDPOINT_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.object_storage,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="TCG_API_DATABASE_URL is unset; no live PostgreSQL to write to",
    ),
    pytest.mark.skipif(
        not ENDPOINT_URL,
        reason="TCG_API_STORAGE_ENDPOINT_URL is unset; no live MinIO to exercise",
    ),
]

#: Every process-wide cache that would otherwise carry one test's event loop
#: into the next — `test_anonymous_journey.py`'s list.
CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# The photographs
# ---------------------------------------------------------------------------
def a_photograph(colour: tuple[int, int, int]) -> bytes:
    """A small JPEG. Its colour is what makes two of them two digests.

    `uq_training_images_sha256` is what turns a second consent into a 409, so a
    test that uploaded the same bytes twice would be asserting the wrong thing
    by accident.
    """
    picture = Image.new("RGB", (48, 32), colour)
    buffer = BytesIO()
    picture.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def two_photographs(seed: int) -> tuple[bytes, bytes]:
    return a_photograph((200, seed % 200, 40)), a_photograph((40, seed % 200, 200))


# ---------------------------------------------------------------------------
# The database and the store, past the API
# ---------------------------------------------------------------------------
def querying(statement: str, **parameters: Any) -> Any:
    async def read() -> Any:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            async with engine.connect() as connection:
                return (await connection.execute(sa.text(statement), parameters)).all()
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


def stored(key: str) -> bool:
    """Whether the object store still holds this key.

    Builds its own client rather than reaching for the process-wide one: a
    cached store belongs to whichever loop first asked for it, and `TestClient`
    runs its own. Every helper in this module keeps that separation.
    """

    async def look() -> bool:
        storage = create_object_storage()
        try:
            await storage.get(StorageKey(key))
        except StorageError:
            return False
        return True

    return run(look)


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
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


@pytest.fixture
def client() -> Iterator[TestClient]:
    """One anonymous user, with their own cookie jar and their own engine."""
    for cached in CACHES:
        cached.cache_clear()
    with TestClient(create_app()) as instance:
        yield instance


@contextmanager
def another_visitor() -> Iterator[TestClient]:
    """A browser that has never seen this service — no cookie, no session.

    `test_feedback_endpoint.py`'s helper, for its reasons: the caches are
    cleared on the way in so this application builds its own engine, and again
    on the way out so an outer client's shutdown cannot dispose this one's.
    """
    for cached in CACHES:
        cached.cache_clear()
    try:
        with TestClient(create_app()) as instance:
            yield instance
    finally:
        for cached in CACHES:
            cached.cache_clear()


@pytest.fixture(autouse=True)
def corpus() -> Iterator[None]:
    """Every consented photograph this module writes, removed afterwards.

    Deleted by `source`, row by row, and never truncated — #196's guard exists
    because a fixture that truncates is one that can lose a corpus. Objects
    first, which is the sweep's own rule and this module's subject.
    """
    yield
    rows = querying(
        "SELECT original_uri, normalized_uri FROM training_images WHERE source = :source",
        source=SOURCE,
    )
    keys = [StorageKey(uri) for row in rows for uri in row if uri]
    if keys:

        async def remove() -> None:
            storage = create_object_storage()
            for key in keys:
                await storage.delete(key)

        run(remove)
    executing("DELETE FROM training_images WHERE source = :source", source=SOURCE)
    executing("TRUNCATE analysis_sessions CASCADE")


def uploaded(client: TestClient, seed: int = 1) -> str:
    """An analysis with both V1 sides stored, which is where consent is asked."""
    analysis_id = client.post("/analyses").json()["id"]
    front, back = two_photographs(seed)
    for side, data in (("front", front), ("back", back)):
        response = client.post(
            f"/analyses/{analysis_id}/images?side={side}",
            content=data,
            headers={"content-type": "image/jpeg"},
        )
        assert response.status_code == 201, response.text
    return str(analysis_id)


def sweep(analysis_id: str) -> None:
    """Expire this analysis's session and run spec §54's sweep over it.

    Backdated rather than waited out, and driven through `purge_expired` rather
    than a `DELETE`: the claim is about what the real sweep reaches.
    """
    executing(
        "UPDATE analysis_sessions "
        "SET created_at = now() - interval '8 days', expires_at = now() - interval '1 day' "
        "WHERE id = (SELECT session_id FROM analyses WHERE id = :id)",
        id=uuid.UUID(analysis_id),
    )

    async def purge() -> None:
        engine = create_async_engine(DATABASE_URL or "")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await purge_expired(session, create_object_storage(), limit=200)
        finally:
            await engine.dispose()

    run(purge)


# ---------------------------------------------------------------------------
# The acceptance criterion
# ---------------------------------------------------------------------------
def test_a_consented_photograph_outlives_its_session_and_can_be_taken_back(
    client: TestClient,
) -> None:
    """#148's acceptance criterion, end to end and in one test.

    A user says yes, their session expires and is swept, their photographs are
    still in the corpus — because a row says so — and weeks later, with no
    session at all, the code they were shown deletes them.
    """
    analysis_id = uploaded(client)

    consented = client.post(f"/analyses/{analysis_id}/training-consent")
    assert consented.status_code == 201, consented.text
    code = consented.json()["withdrawal_code"]
    assert consented.json()["photographs_kept"] == 2
    assert consented.json()["consent_version"] == CONSENT_VERSION

    uploads = [
        row.original_uri
        for row in querying(
            "SELECT original_uri FROM images WHERE analysis_id = :id", id=uuid.UUID(analysis_id)
        )
    ]
    kept = [
        row.original_uri
        for row in querying(
            "SELECT original_uri FROM training_images WHERE source_reference = :id",
            id=analysis_id,
        )
    ]
    assert len(kept) == 2
    # A copy, not the same object: the analysis's key is swept and this one is
    # not, so they cannot be the same key.
    assert set(kept).isdisjoint(uploads)
    assert all(key.startswith("training/") for key in kept)

    sweep(analysis_id)

    assert querying("SELECT id FROM analyses WHERE id = :id", id=uuid.UUID(analysis_id)) == []
    assert not any(stored(key) for key in uploads)
    assert all(stored(key) for key in kept)

    with another_visitor() as later:
        withdrawn = later.delete(f"/training-consent/{code}")
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json() == {"deleted": 2, "kept": 0}

    assert not any(stored(key) for key in kept)
    assert (
        querying("SELECT id FROM training_images WHERE source_reference = :id", id=analysis_id)
        == []
    )


def test_an_unconsented_photograph_is_deleted_on_the_normal_schedule(
    client: TestClient,
) -> None:
    """The other half, and the one the sweep's existing tests already covered.

    Declining costs the user nothing and leaves nothing behind — there is no row
    recording that somebody said no, because a row like that is a record of a
    person's decision kept forever.
    """
    analysis_id = uploaded(client, seed=2)
    uploads = [
        row.original_uri
        for row in querying(
            "SELECT original_uri FROM images WHERE analysis_id = :id", id=uuid.UUID(analysis_id)
        )
    ]

    sweep(analysis_id)

    assert not any(stored(key) for key in uploads)
    assert querying("SELECT id FROM training_images WHERE source = :s", s=SOURCE) == []


# ---------------------------------------------------------------------------
# Spec §29's nine fields, filled at the moment of consent
# ---------------------------------------------------------------------------
def test_the_nine_provenance_fields_are_filled_from_the_grantor(client: TestClient) -> None:
    """ADR 0008: every field is known at acquisition and none is inferred.

    `redistribution_allowed` is `false` on all four approved sources, including
    the photographs this project took itself, because the artwork is nobody's to
    redistribute — least of all the person who consented.
    """
    analysis_id = uploaded(client, seed=3)
    client.post(f"/analyses/{analysis_id}/training-consent")

    rows = querying(
        "SELECT source, source_reference, acquisition_method, license, "
        "commercial_use_allowed, derivative_use_allowed, redistribution_allowed, "
        "permission_notes, acquired_at, physical_copy_id, card_id "
        "FROM training_images WHERE source_reference = :id",
        id=analysis_id,
    )

    assert len(rows) == 2
    for row in rows:
        assert row.source == SOURCE
        assert row.acquisition_method == ACQUISITION_METHOD
        assert row.source_reference == analysis_id
        assert row.license == CONSENT_VERSION
        assert row.commercial_use_allowed is True
        assert row.derivative_use_allowed is True
        assert row.redistribution_allowed is False
        assert row.permission_notes
        assert row.acquired_at is not None
        # ADR 0008's own finding: nothing in an anonymous session identifies the
        # physical copy, and the card is confirmed a screen later.
        assert row.physical_copy_id is None
        assert row.card_id is None


def test_the_code_is_stored_only_as_a_digest(client: TestClient) -> None:
    """A bearer capability, and the column's CHECK is what stops it being stored."""
    analysis_id = uploaded(client, seed=4)
    code = client.post(f"/analyses/{analysis_id}/training-consent").json()["withdrawal_code"]

    rows = querying(
        "SELECT withdrawal_code_hash FROM training_images WHERE source_reference = :id",
        id=analysis_id,
    )

    digests = {row.withdrawal_code_hash for row in rows}
    assert len(digests) == 1
    digest = digests.pop()
    assert digest != code
    assert len(digest) == 64 and digest.islower()


# ---------------------------------------------------------------------------
# Consenting
# ---------------------------------------------------------------------------
def test_the_text_is_served_with_the_version_a_row_records(client: TestClient) -> None:
    """The words and the version travel together, or a row cannot say what it means."""
    response = client.get("/training-consent")

    assert response.status_code == 200
    assert response.json()["version"] == CONSENT_VERSION
    assert len(response.json()["paragraphs"]) >= 5
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_the_text_names_derivative_use_outright(client: TestClient) -> None:
    """ADR 0008's interpretive rule 1 binds a document this project wrote.

    A consent that asked only to "improve the product" would not have granted
    the derivative use a trained model needs, and `commercial_use_allowed` and
    `derivative_use_allowed` are written `true` on the strength of these words.
    """
    words = " ".join(client.get("/training-consent").json()["paragraphs"]).lower()

    assert "derived" in words
    assert "train" in words
    assert "optional" in words


def test_a_second_consent_is_refused_and_mints_no_second_code(client: TestClient) -> None:
    """`uq_training_images_sha256` is the double-tap guard, so no column is."""
    analysis_id = uploaded(client, seed=5)
    # Outside the assert: `python -O` strips the statement, and a first consent
    # that never happened would make the refusal below pass for the wrong
    # reason. `test_datasets_ingestion.py` records the same trap.
    first = client.post(f"/analyses/{analysis_id}/training-consent")
    assert first.status_code == 201

    again = client.post(f"/analyses/{analysis_id}/training-consent")

    assert again.status_code == 409
    kept = querying("SELECT id FROM training_images WHERE source_reference = :id", id=analysis_id)
    assert len(kept) == 2


def test_an_analysis_with_no_photograph_is_refused(client: TestClient) -> None:
    """A code minted over nothing is a code that withdraws nothing."""
    analysis_id = client.post("/analyses").json()["id"]

    response = client.post(f"/analyses/{analysis_id}/training-consent")

    assert response.status_code == 409


def test_another_session_cannot_consent_to_these_photographs(client: TestClient) -> None:
    """Scoped by `owned_analysis`, which is one reading of whose analysis this is."""
    analysis_id = uploaded(client, seed=6)

    with another_visitor() as stranger:
        response = stranger.post(f"/analyses/{analysis_id}/training-consent")

    assert response.status_code == 404
    assert (
        querying("SELECT id FROM training_images WHERE source_reference = :id", id=analysis_id)
        == []
    )


# ---------------------------------------------------------------------------
# Withdrawing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "code",
    ["A3KDM-9F2QT-BXWR7-N0HJ5", "not-a-code", "A3KDM9F2QTBXWR7N0HJ5", "0" * 64],
    ids=["unknown", "malformed", "unseparated-unknown", "a-digest-rather-than-a-code"],
)
def test_every_way_of_missing_is_one_bare_404(client: TestClient, code: str) -> None:
    """Unknown, mistyped and already-withdrawn are told apart nowhere.

    A refusal that distinguished them would tell somebody holding a guess that
    their guess was well formed, which is half of knowing it was real.
    """
    response = client.delete(f"/training-consent/{code}")

    assert response.status_code == 404


def test_a_code_is_spent_by_the_withdrawal(client: TestClient) -> None:
    """Nothing is left to re-read: the rows were the only thing it addressed."""
    analysis_id = uploaded(client, seed=7)
    code = client.post(f"/analyses/{analysis_id}/training-consent").json()["withdrawal_code"]

    # Both calls outside their asserts: the first is what *spends* the code, and
    # `python -O` strips an assert's expression with the statement.
    spent = client.delete(f"/training-consent/{code}")
    again = client.delete(f"/training-consent/{code}")

    assert spent.status_code == 200
    assert again.status_code == 404


def test_a_published_version_holds_its_photograph_back(client: TestClient) -> None:
    """What the consent text says before anybody agrees to it.

    Spec §31 makes a dataset version an immutable record of what a model was
    trained on, and `dataset_members.training_image_id` is `RESTRICT`, so this
    boundary is enforced rather than remembered.
    """
    analysis_id = uploaded(client, seed=8)
    code = client.post(f"/analyses/{analysis_id}/training-consent").json()["withdrawal_code"]
    rows = querying(
        "SELECT id, original_uri FROM training_images WHERE source_reference = :id ORDER BY side",
        id=analysis_id,
    )
    version_id = uuid.uuid4()
    executing(
        "INSERT INTO dataset_versions (id, version, split_seed) "
        "VALUES (:id, 'pokemon-consent-v0.1.0', 1)",
        id=version_id,
    )
    executing(
        "INSERT INTO dataset_members (dataset_version_id, training_image_id, split) "
        "VALUES (:version, :image, 'train')",
        version=version_id,
        image=rows[0].id,
    )

    try:
        withdrawn = client.delete(f"/training-consent/{code}")

        assert withdrawn.status_code == 200
        assert withdrawn.json() == {"deleted": 1, "kept": 1}
        assert stored(rows[0].original_uri)
        assert not stored(rows[1].original_uri)
    finally:
        executing("DELETE FROM dataset_versions WHERE id = :id", id=version_id)


def test_a_withdrawal_that_reaches_nothing_still_answers(client: TestClient) -> None:
    """Every photograph frozen: nothing to delete, and the count says so."""
    analysis_id = uploaded(client, seed=9)
    code = client.post(f"/analyses/{analysis_id}/training-consent").json()["withdrawal_code"]
    rows = querying("SELECT id FROM training_images WHERE source_reference = :id", id=analysis_id)
    version_id = uuid.uuid4()
    executing(
        "INSERT INTO dataset_versions (id, version, split_seed) "
        "VALUES (:id, 'pokemon-consent-v0.2.0', 1)",
        id=version_id,
    )
    for row in rows:
        executing(
            "INSERT INTO dataset_members (dataset_version_id, training_image_id, split) "
            "VALUES (:version, :image, 'train')",
            version=version_id,
            image=row.id,
        )

    try:
        withdrawn = client.delete(f"/training-consent/{code}")

        assert withdrawn.status_code == 200
        assert withdrawn.json() == {"deleted": 0, "kept": 2}
    finally:
        executing("DELETE FROM dataset_versions WHERE id = :id", id=version_id)


def test_withdrawing_needs_no_session(client: TestClient) -> None:
    """Weeks later there is none, and spec §53 forbids the account that would be."""
    analysis_id = uploaded(client, seed=10)
    code = client.post(f"/analyses/{analysis_id}/training-consent").json()["withdrawal_code"]
    sweep(analysis_id)

    with another_visitor() as later:
        response = later.delete(f"/training-consent/{code}")

    assert response.status_code == 200
    assert response.json()["deleted"] == 2
