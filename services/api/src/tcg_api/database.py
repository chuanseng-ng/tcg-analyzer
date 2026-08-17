"""Database configuration, engine and session management for the API service.

Everything the API needs to reach PostgreSQL lives here: settings, the async
engine, the session factory, and a connectivity probe. Schema itself never
arrives through this module — it arrives through reviewed Alembic migrations in
`database/migrations`.

The connection is plain PostgreSQL over asyncpg. Nothing here depends on
Supabase-specific behaviour, which is what keeps the deployment-platform
decision open (spec §8).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tcg_api.config import DATABASE_URL_ENV_VAR, Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "DATABASE_URL_ENV_VAR",
    "check_database_connectivity",
    "create_engine",
    "create_session_factory",
    "get_engine",
    "get_session",
    "get_session_factory",
]


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build an async engine.

    `pool_pre_ping` costs one cheap round trip per checkout and buys immunity to
    connections severed by a restarted database or an idle-timeout proxy — both
    routine in container-based local development and in managed PostgreSQL.

    An unconfigured database raises here rather than at `Settings` construction,
    because a service with no `TCG_API_DATABASE_URL` must still start and report
    itself unready (`routers/readiness.py`). The message names the variable, so
    the readiness log says what to set rather than that something was `None`.
    """
    settings = settings or get_settings()
    if settings.database_url is None:
        raise RuntimeError(
            f"{DATABASE_URL_ENV_VAR} is not set. Point it at the database, e.g. "
            f"{DATABASE_URL_ENV_VAR}=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg. "
            f"See .env.example."
        )
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide engine, created on first use.

    Creation is lazy so that importing this module never requires configuration;
    only actually touching the database does.
    """
    return create_engine()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Bind a session factory to `engine`.

    `expire_on_commit=False` keeps ORM objects usable after the request's commit,
    which the alternative would turn into a lazy load against a closed session.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, created on first use."""
    return create_session_factory(get_engine())


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session for the duration of one request.

    Suitable as a FastAPI dependency (`session: AsyncSession = Depends(get_session)`).
    The `async with` closes and returns the connection to the pool on both the
    success and the exception path.
    """
    async with get_session_factory()() as session:
        yield session


async def check_database_connectivity(engine: AsyncEngine) -> bool:
    """Return whether `engine` can currently execute a trivial statement.

    Returns `False` rather than raising: the only caller is a readiness probe,
    and a probe that propagates an exception reports a 500 when it means to
    report "not ready yet".
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database connectivity check failed", exc_info=True)
        return False
    return True
