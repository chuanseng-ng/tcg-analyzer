"""Structured logging for the API service.

Application logs and uvicorn's own logs are rendered by the same structlog
pipeline. Routing only half the output through structlog would produce a stream
that is neither reliably parseable nor pleasant to read, which defeats the point
of structuring it at all.
"""

from __future__ import annotations

import logging
import sys

import structlog

from tcg_api.config import Settings

__all__ = ["configure_logging"]

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
