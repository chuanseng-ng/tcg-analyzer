"""Application assembly for the API service (spec §8).

Python and FastAPI are intentional: the ML system is Python-native, so the HTTP
surface shares a language with the models it will eventually call.

The application is built by a factory rather than a module-level constant so
tests can construct isolated instances, and so configuration is read at
construction time rather than at import time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

from tcg_api.config import Settings, get_settings
from tcg_api.database import get_engine
from tcg_api.errors import ErrorResponse, install_error_handlers
from tcg_api.logging import configure_logging
from tcg_api.rate_limit import get_redis
from tcg_api.routers import (
    analyses,
    annotation,
    cards,
    catalog,
    economics,
    grading,
    health,
    market,
    readiness,
)
from tcg_api.version import application_version

__all__ = ["create_app"]

#: Declared on every router so the taxonomy reaches the OpenAPI schema, and so
#: `apps/web` has a generated type for the body any endpoint can return. Every
#: endpoint really can 500 — the catch-all guarantees the shape when it does —
#: so documenting it once here is accurate rather than defensive.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_500_INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "The request failed. `code` classifies it; see spec §66.",
    },
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001 - FastAPI passes it
    """Release the connection pools on shutdown.

    `get_engine` is lazily cached, so an engine exists only if something
    actually reached for the database. Checking that before disposing keeps a
    process that never touched PostgreSQL — anything serving only `/health` —
    from constructing an engine purely in order to throw it away, which would
    also make shutdown require configuration that startup did not.

    The rate limiter's Redis client is closed on the same terms, and its cache
    cleared as well: a pooled client that outlived the event loop it was built
    on is the failure `analysis/jobs.py` documents for asyncpg, and a test
    constructing two applications in one process would meet it.
    """
    yield
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()


DESCRIPTION = """\
HTTP surface for TCG Grading Advisor.

Returns a card's identity, its condition, a probability distribution over grades
for each grading company, market values and the economics of grading it.

This is not an official grading service. It does not authenticate cards, and its
predictions are probabilities — never guaranteed grades.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    The OpenAPI schema this produces is the sole source of types for
    ``apps/web`` (ADR 0001), so every route must declare a typed response model.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    version = application_version()
    app = FastAPI(
        title="TCG Grading Advisor API",
        version=version,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    # Before CORS on purpose. `add_middleware` inserts at the front of the
    # stack, so the last one added is the outermost; the catch-all has to sit
    # *inside* `CORSMiddleware` or a 500 leaves without
    # `Access-Control-Allow-Origin` and the browser reads it as a network
    # failure rather than an error it can classify (#260).
    install_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # A cross-origin response exposes only CORS's six safelisted headers to
        # script unless it names more, and `allow_headers` is about the
        # *request*. Without this the browser receives `Retry-After` and refuses
        # to let `apps/web` read it, so the 429 the limiter raises (ADR 0005)
        # reaches the user as "too many requests, unknown wait" — and the only
        # thing left to offer is a button that fires straight back into the
        # limit. Found in a browser; curl reads every header regardless.
        expose_headers=["Retry-After"],
    )

    app.include_router(health.router, responses=ERROR_RESPONSES)
    app.include_router(readiness.router, responses=ERROR_RESPONSES)
    app.include_router(catalog.router, responses=ERROR_RESPONSES)
    app.include_router(cards.router, responses=ERROR_RESPONSES)
    app.include_router(grading.router, responses=ERROR_RESPONSES)
    app.include_router(market.router, responses=ERROR_RESPONSES)
    app.include_router(analyses.router, responses=ERROR_RESPONSES)
    app.include_router(economics.router, responses=ERROR_RESPONSES)
    # Last, and the only one that is not spec §64's. `/internal/annotation` is
    # the annotation tool's surface: in this application because §7 forbids an
    # unnecessary microservice and ADR 0009 declined a second FastAPI app, and in
    # this schema because ADR 0001 makes it the only way `apps/annotation` can
    # learn a shape. What keeps it internal is the prefix an ingress matches.
    app.include_router(annotation.router, responses=ERROR_RESPONSES)

    structlog.get_logger(__name__).info(
        "api.startup",
        application_version=version,
        log_format=settings.log_format,
        log_level=settings.log_level,
        cors_origins=settings.cors_origins,
    )

    return app
