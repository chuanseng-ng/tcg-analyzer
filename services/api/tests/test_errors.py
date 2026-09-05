"""The API's error taxonomy — spec §66.

Defined at the foundation so every later endpoint reports failures in the same
vocabulary. Two things here are load-bearing and easy to undo by accident:

- the eight codes are transcribed verbatim from the specification, so a rename
  is a contract change and must fail a test;
- `insufficient_information` is a legitimate *result*, not a failure. It must be
  expressible in a 200 response, and raising it as an error is a mistake worth
  catching at the point it is made.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tcg_api.app import create_app
from tcg_api.config import Settings
from tcg_api.errors import ApiError, ErrorCode, ErrorResponse, install_error_handlers
from tcg_api.logging import configure_logging

#: The message of the exception the failing route raises. Chosen to look like
#: something that must never be published: an internal path.
SECRET = "/srv/tcg/private/keys.json"

# ---------------------------------------------------------------------------
# The taxonomy — spec §66, verbatim
# ---------------------------------------------------------------------------
SPEC_CODES = (
    "invalid_image",
    "image_quality_failure",
    "card_not_identified",
    "market_data_unavailable",
    "analysis_failed",
    "insufficient_information",
    "provider_error",
    "internal_error",
)


def test_the_taxonomy_is_exactly_the_eight_specified_codes() -> None:
    assert {code.value for code in ErrorCode} == set(SPEC_CODES)


@pytest.mark.parametrize("code", SPEC_CODES)
def test_each_code_serialises_into_the_documented_envelope(code: str) -> None:
    payload = ErrorResponse(code=ErrorCode(code), message="something happened").model_dump()

    assert payload == {"code": code, "message": "something happened", "details": None}


def test_the_envelope_carries_optional_details() -> None:
    payload = ErrorResponse(
        code=ErrorCode.INVALID_IMAGE,
        message="The upload is not an image.",
        details={"content_type": "application/pdf"},
    ).model_dump()

    assert payload["details"] == {"content_type": "application/pdf"}


# ---------------------------------------------------------------------------
# insufficient_information is a result, not a failure
# ---------------------------------------------------------------------------
def test_insufficient_information_can_be_carried_by_a_successful_response() -> None:
    """Spec §2.7 — uncertainty is a valid output, not a 4xx."""
    envelope = ErrorResponse(
        code=ErrorCode.INSUFFICIENT_INFORMATION,
        message="The surface could not be assessed from these photographs.",
    )

    assert envelope.code is ErrorCode.INSUFFICIENT_INFORMATION


def test_raising_insufficient_information_as_an_error_is_refused() -> None:
    """The `raise` never runs — construction is what fails, which is the point.

    Written as a `raise` rather than a bare construction so the statement says
    what the developer being protected here would have written. `ApiError` is
    not a `ValueError`, so the only way this passes is `__init__` refusing.
    """
    with pytest.raises(ValueError, match="result"):
        raise ApiError(ErrorCode.INSUFFICIENT_INFORMATION, "not a failure")


# ---------------------------------------------------------------------------
# ApiError
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        (ErrorCode.INVALID_IMAGE, 400),
        (ErrorCode.IMAGE_QUALITY_FAILURE, 422),
        (ErrorCode.CARD_NOT_IDENTIFIED, 422),
        (ErrorCode.MARKET_DATA_UNAVAILABLE, 503),
        (ErrorCode.PROVIDER_ERROR, 502),
        (ErrorCode.ANALYSIS_FAILED, 500),
        (ErrorCode.INTERNAL_ERROR, 500),
    ],
)
def test_each_code_has_a_default_status(code: ErrorCode, expected_status: int) -> None:
    assert ApiError(code, "message").status_code == expected_status


def test_an_explicit_status_overrides_the_default() -> None:
    assert ApiError(ErrorCode.INVALID_IMAGE, "message", status_code=413).status_code == 413


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@pytest.fixture
def client() -> TestClient:
    """A throwaway app whose routes fail in each of the ways that matter."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/raises-api-error")
    def raises_api_error() -> None:
        raise ApiError(
            ErrorCode.MARKET_DATA_UNAVAILABLE,
            "No usable price for this card.",
            details={"card_id": "abc"},
        )

    @app.get("/raises-anything")
    def raises_anything() -> None:
        raise LookupError(f"the secret is {SECRET}")

    return TestClient(app, raise_server_exceptions=False)


def test_an_api_error_becomes_its_code_and_status(client: TestClient) -> None:
    response = client.get("/raises-api-error")

    assert response.status_code == 503
    assert response.json() == {
        "code": "market_data_unavailable",
        "message": "No usable price for this card.",
        "details": {"card_id": "abc"},
    }


def test_an_unhandled_exception_becomes_internal_error(client: TestClient) -> None:
    response = client.get("/raises-anything")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


def test_an_unhandled_exception_leaks_nothing(client: TestClient) -> None:
    """Not the message, the exception class, the module path or a traceback."""
    body = client.get("/raises-anything").text

    for leak in ("LookupError", SECRET, "Traceback", "test_errors.py", "raises_anything"):
        assert leak not in body, f"the response body leaked {leak!r}"


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_an_unhandled_exception_is_logged_with_its_traceback(
    log_format: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The detail belongs in the log, which is the reason it may leave the body.

    Asserted against the configured pipeline in both renderings rather than
    against the logger call, because a traceback the JSON renderer silently
    drops would leave the service with no record of why a request 500'd — and
    the body deliberately says nothing.
    """
    configure_logging(Settings(_env_file=None, log_format=log_format))

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/raises-anything")
    def raises_anything() -> None:
        raise LookupError(f"the secret is {SECRET}")

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/raises-anything")

    logged = capsys.readouterr().out
    assert "LookupError" in logged
    assert SECRET in logged
    assert "Traceback" in logged


def test_an_unhandled_exception_is_readable_cross_origin() -> None:
    """Starlette runs an `Exception` handler in its outermost layer, outside CORS.

    A 500 produced there carries no `Access-Control-Allow-Origin`, so a
    cross-origin `fetch` in `apps/web` *rejects* instead of resolving, and every
    classifier reads the rejection as "the service could not be reached". The
    catch-all therefore has to sit inside `CORSMiddleware` — which, because
    `add_middleware` inserts at the front, means being added *before* it. Asserted
    on the real application so the order in `create_app` is what is pinned.
    """
    app = create_app()

    @app.get("/raises-anything")
    def raises_anything() -> None:
        raise LookupError(f"the secret is {SECRET}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/raises-anything", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    for leak in ("LookupError", SECRET, "Traceback"):
        assert leak not in response.text, f"the response body leaked {leak!r}"
