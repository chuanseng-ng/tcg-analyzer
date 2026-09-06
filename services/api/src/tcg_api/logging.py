"""Structured logging for the API service.

Application logs and uvicorn's own logs are rendered by the same structlog
pipeline. Routing only half the output through structlog would produce a stream
that is neither reliably parseable nor pleasant to read, which defeats the point
of structuring it at all.
"""

from __future__ import annotations

import logging
import sys
import time
from uuid import uuid4

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tcg_api.config import Settings

__all__ = ["RequestLogMiddleware", "configure_logging"]

#: Marks the handler this module owns, so reconfiguring replaces it instead of
#: stacking a second copy and duplicating every line.
_HANDLER_NAME = "tcg-api-structlog"

#: uvicorn installs its own handlers and disables propagation; both are undone
#: so its records reach the root handler configured below.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib logging root. Safe to call repeatedly."""
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # Renders `exc_info` into a traceback string. Without it the console
        # renderer still formats exceptions but the JSON renderer does not, so
        # a 500 would be logged with no record of its cause — and the error
        # handler deliberately keeps that cause out of the response body.
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        # Records emitted by stdlib loggers (uvicorn, third parties) have not
        # been through the structlog processors, so they are applied here.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    handler.set_name(_HANDLER_NAME)

    root = logging.getLogger()
    for existing in [h for h in root.handlers if h.name == _HANDLER_NAME]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # uvicorn's access line says the same thing as `api.request_completed`
    # below, with the raw path where that line carries the route template
    # (spec §54, #266). Its warnings and errors still come through.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


class RequestLogMiddleware:
    """One `api.request_completed` line per request, joined to every other line by a request id.

    The id is generated here and never read from the client — a client that
    could choose it could forge a join key into another request's lines. It is
    bound into contextvars for the request, so `merge_contextvars` puts it on
    every line the request logs, and echoed as `X-Request-Id` so a user can
    quote it. The line carries the route *template* (`/analyses/{analysis_id}`),
    never the path: the template counts, the path names a user's analysis.

    Pure ASGI, like `errors._UnexpectedErrorMiddleware`, and outermost: a 404
    and a preflight are requests too, and a contextvar bound out here is what
    the catch-all's `api.unhandled_exception` inside picks up. The line is
    written in a `finally` — the catch-all has already answered the 500 and
    re-raised by the time control comes back, so the status is observed, not
    inferred, and the exception keeps going.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        status: int | None = None

        async def _send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        started = time.perf_counter()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            await self.app(scope, receive, _send)
        finally:
            # Set by FastAPI's router on a match; absent on a 404 or a preflight.
            route = scope.get("route")
            structlog.get_logger(__name__).info(
                "api.request_completed",
                method=scope["method"],
                route=None if route is None else route.path,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()
