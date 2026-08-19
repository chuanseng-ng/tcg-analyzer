"""Object-storage wiring: configuration in, one adapter out.

The behaviour worth pinning here is not S3 — `packages/shared` owns that — but
the same absent-versus-malformed distinction the database already makes. An
unconfigured store must let the service start and report itself unready; a
*malformed* endpoint must stop startup with a message naming the variable.

The connectivity probe gets the same treatment as the database's: it returns
`False` instead of raising, because its only caller is a readiness endpoint and
a probe that propagates answers 500 when it means 503.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from tcg_api.config import (
    STORAGE_ACCESS_KEY_ID_ENV_VAR,
    STORAGE_BUCKET_ENV_VAR,
    STORAGE_ENDPOINT_URL_ENV_VAR,
    STORAGE_SECRET_ACCESS_KEY_ENV_VAR,
    Settings,
)
from tcg_api.storage import (
    check_object_storage_connectivity,
    create_object_storage,
    get_object_storage,
)
from tcg_shared.storage import ObjectNotFound, StorageKey, StorageUnavailable
from tcg_shared.storage.s3 import S3ObjectStorage

CONFIGURED = {
    "storage_endpoint_url": "http://localhost:9000",
    "storage_bucket": "tcg-local",
    "storage_access_key_id": "tcg",
    "storage_secret_access_key": "tcglocaldev",
}


def configured(**overrides: object) -> Settings:
    return Settings(_env_file=None, **(CONFIGURED | overrides))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_storage_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STORAGE_ENDPOINT_URL_ENV_VAR, "http://minio.test:9000")
    monkeypatch.setenv(STORAGE_BUCKET_ENV_VAR, "cards")

    settings = Settings(_env_file=None)

    assert settings.storage_endpoint_url == "http://minio.test:9000"
    assert settings.storage_bucket == "cards"


@pytest.mark.usefixtures("unconfigured_environment")
def test_storage_is_absent_rather_than_fatal_when_unconfigured() -> None:
    """A service with no bucket is unready, not crashing.

    `unconfigured_environment` clears the real `TCG_API_STORAGE_*` variables:
    `_env_file=None` suppresses the file and nothing else, so a shell set up to
    run `pytest -m object_storage` would otherwise configure the storage this
    test is asserting the absence of.
    """
    settings = Settings(_env_file=None)

    assert settings.storage_bucket is None
    assert settings.storage_endpoint_url is None


@pytest.mark.parametrize("rejected", ["localhost:9000", "not a url", "ftp://minio:9000", "//minio"])
def test_a_malformed_storage_endpoint_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch, rejected: str
) -> None:
    """The usual mistake is a missing scheme, and botocore's own message hides it."""
    monkeypatch.setenv(STORAGE_ENDPOINT_URL_ENV_VAR, rejected)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert STORAGE_ENDPOINT_URL_ENV_VAR in str(excinfo.value)


def test_a_non_positive_signed_url_lifetime_is_rejected() -> None:
    """A URL that expires on issue is a configuration mistake, not a strict policy."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, storage_signed_url_ttl_seconds=0)


def test_the_secret_key_is_not_printed_by_accident() -> None:
    """`SecretStr` is what stops a credential reaching a log line (spec §77)."""
    settings = configured()

    assert "tcglocaldev" not in repr(settings)
    assert settings.storage_secret_access_key is not None
    assert settings.storage_secret_access_key.get_secret_value() == "tcglocaldev"


# ---------------------------------------------------------------------------
# create_object_storage
# ---------------------------------------------------------------------------


def test_a_configured_service_gets_an_s3_adapter() -> None:
    storage = create_object_storage(configured())

    assert isinstance(storage, S3ObjectStorage)
    assert storage.bucket == "tcg-local"


def test_an_endpoint_is_optional_because_real_aws_needs_none() -> None:
    storage = create_object_storage(configured(storage_endpoint_url=None))

    assert isinstance(storage, S3ObjectStorage)


def test_building_a_client_reaches_no_network() -> None:
    """Construction must succeed while the store is down.

    Otherwise the readiness probe could not tell "misconfigured" from
    "unreachable" — both would fail in the same place.
    """
    create_object_storage(configured(storage_endpoint_url="http://127.0.0.1:1"))


@pytest.mark.parametrize(
    ("missing", "variable"),
    [
        ("storage_bucket", STORAGE_BUCKET_ENV_VAR),
        ("storage_access_key_id", STORAGE_ACCESS_KEY_ID_ENV_VAR),
        ("storage_secret_access_key", STORAGE_SECRET_ACCESS_KEY_ENV_VAR),
    ],
)
def test_unconfigured_storage_raises_a_message_naming_the_variable(
    missing: str, variable: str
) -> None:
    """The readiness log should say what to set, not that something was `None`."""
    with pytest.raises(RuntimeError) as excinfo:
        create_object_storage(configured(**{missing: None}))

    assert variable in str(excinfo.value)
    assert ".env.example" in str(excinfo.value)


@pytest.mark.usefixtures("unconfigured_environment")
def test_every_missing_variable_is_named_at_once() -> None:
    """Missing, and therefore only missing once the environment says so."""
    with pytest.raises(RuntimeError) as excinfo:
        create_object_storage(Settings(_env_file=None))

    message = str(excinfo.value)
    for variable in (
        STORAGE_BUCKET_ENV_VAR,
        STORAGE_ACCESS_KEY_ID_ENV_VAR,
        STORAGE_SECRET_ACCESS_KEY_ENV_VAR,
    ):
        assert variable in message


def test_get_object_storage_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in CONFIGURED.items():
        monkeypatch.setenv(f"TCG_API_{key.upper()}", str(value))
    get_object_storage.cache_clear()

    assert get_object_storage() is get_object_storage()


# ---------------------------------------------------------------------------
# check_object_storage_connectivity
# ---------------------------------------------------------------------------


class _Answering:
    """A store that answers the probe by reporting the key is absent."""

    async def get(self, key: StorageKey) -> bytes:
        raise ObjectNotFound(f"no object is stored under {key}")


class _Unreachable:
    async def get(self, key: StorageKey) -> bytes:
        raise StorageUnavailable("no route to the object store")


class _Populated:
    async def get(self, key: StorageKey) -> bytes:
        return b"unexpected but still an answer"


@pytest.mark.parametrize(
    ("store", "reachable"),
    [(_Answering(), True), (_Populated(), True), (_Unreachable(), False)],
)
def test_the_probe_reports_reachability(store: object, reachable: bool) -> None:
    """A miss is the store answering; only a failure to reach it counts as down."""
    assert asyncio.run(check_object_storage_connectivity(store)) is reachable  # type: ignore[arg-type]


def test_the_probe_returns_false_instead_of_raising() -> None:
    """A readiness probe that propagates reports 500 when it means 503."""

    class Exploding:
        async def get(self, key: StorageKey) -> bytes:
            raise RuntimeError("something entirely unexpected")

    assert asyncio.run(check_object_storage_connectivity(Exploding())) is False  # type: ignore[arg-type]
