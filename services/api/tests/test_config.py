"""Settings are environment-driven so a container needs no code change to retune.

The CORS default exists because the Next.js dev server on :3000 must reach this
API on :8000.

`database_url` is deliberately optional. A service with no database
configuration must still start and report itself unready — see
`test_readiness_wiring.py`. What fails fast is a *malformed* value: a URL that
cannot be parsed, or one naming a synchronous driver, is an operator mistake
that should surface at startup rather than as a confusing error on the first
query.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tcg_api.config import DATABASE_URL_ENV_VAR, Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_log_level_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCG_API_LOG_LEVEL", "DEBUG")

    assert get_settings().log_level == "DEBUG"


def test_log_format_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCG_API_LOG_FORMAT", "console")

    assert get_settings().log_format == "console"


def test_cors_origins_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCG_API_CORS_ORIGINS", '["https://example.test"]')

    assert get_settings().cors_origins == ["https://example.test"]


def test_defaults_serve_local_development() -> None:
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert "http://localhost:3000" in settings.cors_origins


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_unknown_environment_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCG_API_NOT_A_SETTING", "x")

    assert get_settings().log_level == "INFO"


# ---------------------------------------------------------------------------
# database_url — folded in from the former `database.DatabaseSettings`
# ---------------------------------------------------------------------------
ASYNC_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"


def test_database_url_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV_VAR, ASYNC_URL)

    assert get_settings().database_url == ASYNC_URL


def test_database_url_is_absent_rather_than_fatal_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured database is an unready service, not a crashing one."""
    monkeypatch.delenv(DATABASE_URL_ENV_VAR, raising=False)

    assert Settings(_env_file=None).database_url is None


def test_an_unparseable_database_url_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV_VAR, "this is not a url")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert DATABASE_URL_ENV_VAR in str(excinfo.value)


def test_a_synchronous_driver_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_async_engine` cannot use psycopg2; say so now, not on first query."""
    monkeypatch.setenv(DATABASE_URL_ENV_VAR, "postgresql://unused:unused@127.0.0.1:1/unused")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    message = str(excinfo.value)
    assert DATABASE_URL_ENV_VAR in message
    assert "async" in message.lower()


def test_an_unknown_driver_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATABASE_URL_ENV_VAR, "nosuchdriver://unused@127.0.0.1/unused")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert DATABASE_URL_ENV_VAR in str(excinfo.value)
