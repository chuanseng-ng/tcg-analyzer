"""Alembic environment.

Async, because the API service talks to PostgreSQL over asyncpg and running
migrations through a second driver would mean a second connection string to keep
correct.

The URL is read from the environment, never from `alembic.ini`, so no credential
is ever committed.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# The schema's source of truth, so `alembic revision --autogenerate` compares a
# database against the tables the code actually declares. `alembic.ini` sets
# `prepend_sys_path = .`, and `tcg_api` is an editable workspace install, so this
# import works from the repository root without any path juggling.
#
# Autogenerate proposes dropping anything it finds and does not see here, so
# every future domain table must be declared in a module reachable from it — not
# created by hand-written DDL alone.
from tcg_api.catalog.tables import metadata as target_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DATABASE_URL_ENV_VAR = "TCG_API_DATABASE_URL"


def database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV_VAR)
    if not url:
        raise RuntimeError(
            f"{DATABASE_URL_ENV_VAR} is not set. Point it at the database to "
            f"migrate, e.g. "
            f"{DATABASE_URL_ENV_VAR}=postgresql+asyncpg://tcg:tcg@localhost:5432/tcg. "
            f"Start a local PostgreSQL with "
            f"`docker compose -f infrastructure/local/docker-compose.yml up -d`."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (`alembic upgrade head --sql`)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live database."""
    section = config.get_section(config.config_ini_section, {})
    # `%` is configparser's interpolation character, and passwords contain it.
    section["sqlalchemy.url"] = database_url().replace("%", "%%")

    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
