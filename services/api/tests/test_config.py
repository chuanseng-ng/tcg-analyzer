"""Settings are environment-driven so a container needs no code change to retune.

The CORS default exists because the Next.js dev server on :3000 must reach this
API on :8000.
"""

from __future__ import annotations

import pytest

from tcg_api.config import Settings, get_settings


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
