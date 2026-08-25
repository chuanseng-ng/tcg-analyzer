"""Unit tests for `tcg_api.database`.

Every test here runs without PostgreSQL. Engines are constructed but never
connected to; the session tests use a real `AsyncSession` subclass rather than a
mock so the assertions are about SQLAlchemy's actual behaviour.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
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


# ---------------------------------------------------------------------------
# execute
#
# The one place a driver failure stops being the driver's. Hoisted from four
# near-identical wrappers — `catalog/cards.py`, `catalog/versions.py`,
# `analysis/sessions.py` and `grading/rules.py` — when `market/snapshots.py`
# would have been the fifth (#56).
# ---------------------------------------------------------------------------
class StoreUnavailable(ConnectionError):
    """Stands in for the four real domain exceptions, which behave identically."""


class FailingSession:
    """Enough of an `AsyncSession` to fail, with the failure chosen per test."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def execute(self, statement: object) -> object:
        raise self.error


def failure(error: Exception) -> StoreUnavailable:
    session = cast(AsyncSession, FailingSession(error))
    with pytest.raises(StoreUnavailable) as raised:
        asyncio.run(
            database.execute(
                session,
                text("SELECT 1"),
                unavailable=StoreUnavailable,
                message="The store could not be reached.",
            )
        )
    return raised.value


def test_execute_translates_a_sqlalchemy_error_into_the_named_exception() -> None:
    """The driver's exception becomes the store's, and stays on `__cause__`."""
    driver_error = OperationalError("SELECT 1", {}, Exception("connection closed"))

    translated = failure(driver_error)

    assert str(translated) == "The store could not be reached."
    assert translated.__cause__ is driver_error
    assert "asyncpg" not in str(translated)


def test_execute_translates_a_refused_connection_too() -> None:
    """The limb all four hoisted wrappers documented separately.

    asyncpg opens its socket through asyncio, so a refused connection raises
    `ConnectionRefusedError` before the dialect has anything to wrap — it never
    becomes a `SQLAlchemyError` at all. Catching only that one would let exactly
    the case these exceptions exist to name escape untranslated.
    """
    refused = ConnectionRefusedError("[Errno 111] Connect call failed")

    translated = failure(refused)

    assert str(translated) == "The store could not be reached."
    assert translated.__cause__ is refused


def test_execute_returns_the_result_untouched() -> None:
    """No wrapping on the success path: callers still call `.one_or_none()`."""

    class Result:
        pass

    expected = Result()

    class Session:
        async def execute(self, statement: object) -> object:
            return expected

    result = asyncio.run(
        database.execute(
            cast(AsyncSession, Session()),
            text("SELECT 1"),
            unavailable=StoreUnavailable,
            message="unused",
        )
    )

    assert result is expected


def test_execute_does_not_translate_a_programming_mistake() -> None:
    """A `TypeError` from a malformed statement is a bug, not an outage."""
    with pytest.raises(TypeError, match="not a statement"):
        asyncio.run(
            database.execute(
                cast(AsyncSession, FailingSession(TypeError("not a statement"))),
                text("SELECT 1"),
                unavailable=StoreUnavailable,
                message="unused",
            )
        )
