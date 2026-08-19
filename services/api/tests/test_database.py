"""Unit tests for `tcg_api.database`.

Every test here runs without PostgreSQL. Engines are constructed but never
connected to; the session tests use a real `AsyncSession` subclass rather than a
mock so the assertions are about SQLAlchemy's actual behaviour.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tcg_api import database
from tcg_api.config import DATABASE_URL_ENV_VAR, Settings

# A syntactically valid URL that is never connected to.
UNUSED_URL = "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused"


# ---------------------------------------------------------------------------
# Engine construction
#
# Settings themselves are tested in `test_config.py`; `database_url` lives on
# the one service-wide `Settings` object rather than a second settings class.
# ---------------------------------------------------------------------------
def test_create_engine_uses_the_configured_url_and_pre_ping() -> None:
    engine = database.create_engine(Settings(_env_file=None, database_url=UNUSED_URL))

    assert engine.url.render_as_string(hide_password=False) == UNUSED_URL
    assert engine.pool._pre_ping is True


@pytest.mark.usefixtures("unconfigured_environment")
def test_create_engine_without_configuration_names_the_variable() -> None:
    """The readiness probe turns this into a 503, so the message reaches a log.

    `unconfigured_environment` is what makes "without configuration" true.
    `_env_file=None` suppresses the file alone, so a shell that exported
    `TCG_API_DATABASE_URL` — which is exactly what CLAUDE.md tells a developer
    to do before running the integration tests — would otherwise supply the
    setting this test is asserting the absence of.
    """
    with pytest.raises(RuntimeError, match=DATABASE_URL_ENV_VAR):
        database.create_engine(Settings(_env_file=None))


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------
class RecordingSession(AsyncSession):
    """A real AsyncSession that records whether it was closed."""

    closed: bool = False

    async def close(self) -> None:
        RecordingSession.closed = True
        await super().close()


@pytest.fixture
def recording_sessions(monkeypatch: pytest.MonkeyPatch):
    RecordingSession.closed = False
    engine = create_async_engine(UNUSED_URL)
    factory = async_sessionmaker(engine, class_=RecordingSession, expire_on_commit=False)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)

    yield RecordingSession

    asyncio.run(engine.dispose())


def test_get_session_yields_a_session_and_closes_it(
    recording_sessions: type[RecordingSession],
) -> None:
    async def scenario() -> AsyncSession:
        generator = database.get_session()
        session = await anext(generator)
        assert isinstance(session, AsyncSession)
        assert recording_sessions.closed is False
        with pytest.raises(StopAsyncIteration):
            await anext(generator)
        return session

    asyncio.run(scenario())

    assert recording_sessions.closed is True


def test_get_session_closes_the_session_when_the_caller_raises(
    recording_sessions: type[RecordingSession],
) -> None:
    async def scenario() -> None:
        generator = database.get_session()
        await anext(generator)
        with pytest.raises(RuntimeError, match="request blew up"):
            await generator.athrow(RuntimeError("request blew up"))

    asyncio.run(scenario())

    assert recording_sessions.closed is True


# ---------------------------------------------------------------------------
# check_database_connectivity
# ---------------------------------------------------------------------------
def test_check_database_connectivity_returns_false_instead_of_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Port 1 on loopback refuses connections, so this exercises the real
    # failure path rather than a stubbed exception.
    engine = create_async_engine(UNUSED_URL, connect_args={"timeout": 1})

    result = asyncio.run(database.check_database_connectivity(engine))

    assert result is False
    assert any("database" in record.message.lower() for record in caplog.records)
