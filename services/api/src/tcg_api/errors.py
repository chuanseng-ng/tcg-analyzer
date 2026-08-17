"""The API's error taxonomy and problem-detail envelope (spec §66).

Defined at the foundation rather than per endpoint, so every later endpoint
reports failures in one vocabulary and `apps/web` needs one error parser.

The eight codes are transcribed verbatim from the specification. Adding a ninth
is a specification change, not a local decision.

**`insufficient_information` is not a failure.** It is a legitimate product
outcome (spec §2.7, §16, §44): the honest answer when the photographs do not
support a conclusion. It therefore belongs in a 200 response body, carried by
whatever model the endpoint returns, and `ApiError` refuses to be constructed
with it — a raise would turn "we do not know" into "something went wrong", which
is exactly the confident-but-wrong behaviour the specification forbids.

`market_data_unavailable` and `provider_error` are deliberately distinct: the
first means there is no usable price, the second that the call failed. The
economic engine treats them differently.

**Shape.** `{code, message, details}`, as the issue specifies — RFC 9457's field
names (`type`, `title`, `detail`) are not used, because a stable machine-readable
`code` drawn from a closed enum is what the frontend and the economic engine
branch on, and `application/json` keeps one media type across every response.

**Scope.** Handlers here cover `ApiError` and the catch-all. FastAPI's own
`HTTPException` and request-validation responses are left alone: a missing route
or a malformed query string is a transport-level failure with no §66 meaning,
and forcing one into `internal_error` would be a lie in the one field callers
are meant to trust. Endpoints that need those cases to speak the taxonomy raise
`ApiError` themselves.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

__all__ = [
    "ApiError",
    "ErrorCode",
    "ErrorResponse",
    "install_error_handlers",
]

logger = structlog.get_logger(__name__)


class ErrorCode(StrEnum):
    """Spec §66, verbatim. The closed set of things that can go wrong."""

    INVALID_IMAGE = "invalid_image"
    IMAGE_QUALITY_FAILURE = "image_quality_failure"
    CARD_NOT_IDENTIFIED = "card_not_identified"
    MARKET_DATA_UNAVAILABLE = "market_data_unavailable"
    ANALYSIS_FAILED = "analysis_failed"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    PROVIDER_ERROR = "provider_error"
    INTERNAL_ERROR = "internal_error"


#: The status each failure code answers with unless an endpoint overrides it.
#:
#: `insufficient_information` is absent on purpose — it has no failure status
#: because it is not a failure. See the module docstring.
DEFAULT_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_IMAGE: status.HTTP_400_BAD_REQUEST,
    ErrorCode.IMAGE_QUALITY_FAILURE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.CARD_NOT_IDENTIFIED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.MARKET_DATA_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.PROVIDER_ERROR: status.HTTP_502_BAD_GATEWAY,
    ErrorCode.ANALYSIS_FAILED: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

GENERIC_INTERNAL_MESSAGE = "The request could not be completed."


class ErrorResponse(BaseModel):
    """The one error body this API produces.

    `apps/web` generates its types from this model (ADR 0001), so the field
    names are a public contract.
    """

    code: ErrorCode = Field(
        description="Machine-readable classification from the spec §66 taxonomy.",
    )
    message: str = Field(
        description="Human-readable summary. Safe to show a user; never contains internal detail.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context, e.g. which field was rejected.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "code": "market_data_unavailable",
                "message": "No usable price is available for this card.",
                "details": {"card_id": "base1-4"},
            }
        }
    }


class ApiError(Exception):
    """A failure that already knows how it should be reported.

    Raise this rather than assembling an envelope, so the status, the code and
    the body cannot drift apart between endpoints.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code is ErrorCode.INSUFFICIENT_INFORMATION and status_code is None:
            raise ValueError(
                "insufficient_information is a result, not a failure (spec §2.7). "
                "Return it in the endpoint's own response model rather than raising it."
            )

        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code or DEFAULT_STATUS[code]

    def as_response(self) -> JSONResponse:
        """Render this error as the documented envelope."""
        body = ErrorResponse(code=self.code, message=self.message, details=self.details)
        return JSONResponse(status_code=self.status_code, content=body.model_dump(mode="json"))


def install_error_handlers(app: FastAPI) -> None:
    """Register the taxonomy's handlers on `app`."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        logger.warning(
            "api.error",
            code=exc.code.value,
            status_code=exc.status_code,
            path=request.url.path,
        )
        return exc.as_response()

    # Catching bare `Exception` is the point of this one: nothing may reach a
    # client unshaped, and an exception nobody anticipated is exactly the case
    # where FastAPI would otherwise answer with its own untyped body.
    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # `exc_info` puts the traceback, the exception type and the message in
        # the log — which is precisely why none of them go in the body. A client
        # learns that the request failed; an operator learns why.
        logger.error("api.unhandled_exception", path=request.url.path, exc_info=exc)
        return ApiError(
            ErrorCode.INTERNAL_ERROR,
            GENERIC_INTERNAL_MESSAGE,
        ).as_response()
