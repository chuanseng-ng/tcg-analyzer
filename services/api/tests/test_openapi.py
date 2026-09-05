"""The OpenAPI schema is the sole source of frontend types — ADR 0001.

`apps/web` generates its TypeScript types from this schema rather than importing
a shared package, so an endpoint that is served but undocumented, or documented
without a response model, is a broken contract rather than a cosmetic omission.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import version

from fastapi.testclient import TestClient
from tcg_api.app import create_app


def test_openapi_json_is_served() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200


def test_openapi_generation_does_not_raise() -> None:
    assert create_app().openapi()


def test_openapi_documents_the_health_path() -> None:
    schema = create_app().openapi()

    assert "/health" in schema["paths"]
    assert "get" in schema["paths"]["/health"]


def test_openapi_documents_the_health_response_schema() -> None:
    """A bare dict response would leave apps/web without a generated type."""
    schema = create_app().openapi()

    content = schema["paths"]["/health"]["get"]["responses"]["200"]["content"]
    reference = content["application/json"]["schema"]["$ref"]
    model_name = reference.rsplit("/", 1)[-1]

    properties = schema["components"]["schemas"][model_name]["properties"]
    assert set(properties) == {"status", "application_version"}


def test_openapi_reports_the_application_version() -> None:
    schema = create_app().openapi()

    assert schema["info"]["version"] == version("tcg-api")


def test_openapi_documents_the_catalog_version_path() -> None:
    """Spec §64's endpoint list is conceptual; this is an addition to it (#27).

    `apps/web` generates its types from this schema, so an endpoint that is
    served but undocumented is a contract the frontend cannot compile against.
    """
    schema = create_app().openapi()

    assert "/catalog/version" in schema["paths"]
    assert "get" in schema["paths"]["/catalog/version"]


def test_openapi_documents_the_card_detail_path() -> None:
    """Spec §64 names `GET /cards/{id}`; #29 serves it.

    The path is templated in the schema, so it appears under the parameter's
    name rather than the spec's `{id}`.
    """
    schema = create_app().openapi()

    assert "/cards/{card_id}" in schema["paths"]
    assert "get" in schema["paths"]["/cards/{card_id}"]


def test_openapi_documents_the_card_search_path() -> None:
    """Spec §64 names `GET /cards/search`; #28 serves it.

    A literal path, so it appears verbatim — and it has to be declared before
    `/cards/{card_id}` or that route swallows it. See `routers/cards.py`.
    """
    schema = create_app().openapi()

    assert "/cards/search" in schema["paths"]
    assert "get" in schema["paths"]["/cards/search"]


def test_openapi_documents_the_grading_companies_path() -> None:
    """Spec §64 names `GET /grading-companies`; #48 serves it.

    A literal path with no parameters, so it appears verbatim.
    """
    schema = create_app().openapi()

    assert "/grading-companies" in schema["paths"]
    assert "get" in schema["paths"]["/grading-companies"]


def test_openapi_documents_the_card_market_path() -> None:
    """Spec §64 names `GET /cards/{id}/market`; #56 serves it.

    Mounted by a second router on the same `/cards` prefix. It has one segment
    more than `/cards/{card_id}`, so the two cannot shadow each other whatever
    order they include in.
    """
    schema = create_app().openapi()

    assert "/cards/{card_id}/market" in schema["paths"]
    assert "get" in schema["paths"]["/cards/{card_id}/market"]


def test_openapi_documents_the_analysis_paths() -> None:
    """Spec §64 names `POST /analyses` and `GET /analyses/{id}`; #32 serves them.

    The session cookie is deliberately *not* in the schema as a parameter: it is
    set and read by the browser, and declaring it would invite a generated
    client to try to send one itself.
    """
    schema = create_app().openapi()

    assert "post" in schema["paths"]["/analyses"]
    assert "get" in schema["paths"]["/analyses/{analysis_id}"]


def test_openapi_documents_the_image_upload_path() -> None:
    """Spec §64 names `POST /analyses/{id}/images`; #33 serves it."""
    schema = create_app().openapi()

    assert "post" in schema["paths"]["/analyses/{analysis_id}/images"]


def test_openapi_documents_the_upload_body_as_binary() -> None:
    """The upload takes the image as the request body, not as a multipart part.

    Declared through `openapi_extra`, because FastAPI has no body parameter to
    infer it from — the endpoint reads the stream itself so the byte limit
    applies while the upload arrives. Without this a generated client would have
    no idea what to send.
    """
    operation = create_app().openapi()["paths"]["/analyses/{analysis_id}/images"]["post"]

    body = operation["requestBody"]
    assert body["required"] is True
    assert set(body["content"]) == {"image/jpeg", "image/png"}
    assert body["content"]["image/jpeg"]["schema"] == {"type": "string", "format": "binary"}


def test_openapi_documents_the_upload_rejection() -> None:
    """A rejected upload is `invalid_image` inside the §66 envelope.

    Unlike the 404, the 409 and the 429 on the same router: the taxonomy has a
    code that says exactly this, so there is no reason to answer outside it.
    """
    operation = create_app().openapi()["paths"]["/analyses/{analysis_id}/images"]["post"]

    content = operation["responses"]["400"]["content"]["application/json"]
    assert content["schema"]["$ref"].endswith("/ErrorResponse")


def test_openapi_documents_the_throttled_response() -> None:
    """#98 limits the three writes spec §55 names, and says so in the contract.

    §55 names analysis endpoints *and image uploads*, so #33's endpoint carries
    the same dependency and shares the same bucket. Documented without a model,
    exactly as the 404 and the 409 are: a 429 is a transport-level failure
    outside the spec §66 envelope (ADR 0005), so there is no `ErrorResponse` for
    a generated client to expect.
    """
    paths = create_app().openapi()["paths"]

    assert "429" in paths["/analyses"]["post"]["responses"]
    assert "429" in paths["/analyses/{analysis_id}/run"]["post"]["responses"]
    assert "429" in paths["/analyses/{analysis_id}/images"]["post"]["responses"]
    assert "429" not in paths["/analyses/{analysis_id}"]["get"]["responses"]
    assert "429" not in paths["/cards/search"]["get"]["responses"]
    assert "429" not in paths["/cards/{card_id}/market"]["get"]["responses"]


# ---------------------------------------------------------------------------
# The error taxonomy — spec §66
# ---------------------------------------------------------------------------
def test_openapi_documents_the_error_envelope() -> None:
    """`apps/web` needs a generated type for the body every endpoint can return."""
    schemas = create_app().openapi()["components"]["schemas"]

    assert set(schemas["ErrorResponse"]["properties"]) == {"code", "message", "details"}


def test_openapi_documents_all_eight_error_codes() -> None:
    """The acceptance criterion of #19: the whole taxonomy is in the contract."""
    schemas = create_app().openapi()["components"]["schemas"]

    assert set(schemas["ErrorCode"]["enum"]) == {
        "invalid_image",
        "image_quality_failure",
        "card_not_identified",
        "market_data_unavailable",
        "analysis_failed",
        "insufficient_information",
        "provider_error",
        "internal_error",
    }


def test_every_documented_path_can_answer_with_the_envelope() -> None:
    """Any endpoint may fail; the schema should not pretend otherwise."""
    paths = create_app().openapi()["paths"]

    for path, operations in paths.items():
        for method, operation in operations.items():
            content = operation["responses"]["500"]["content"]["application/json"]
            assert content["schema"]["$ref"].endswith("/ErrorResponse"), f"{method} {path}"


# ---------------------------------------------------------------------------
# The economics — spec §64, §41 (#65)
# ---------------------------------------------------------------------------
def test_openapi_documents_the_economic_configuration_path() -> None:
    """Spec §64's endpoint list is conceptual; this is an addition to it (#65)."""
    paths = create_app().openapi()["paths"]

    assert "post" in paths["/analyses/{analysis_id}/economic-configuration"]
    assert "get" in paths["/analyses/{analysis_id}/results"]


def test_openapi_names_the_two_profit_figures_separately() -> None:
    """#65's acceptance criterion, read off the contract `apps/web` compiles against.

    Spec §41 says the distinction between the incremental grading decision and
    the investment return "must be implemented rather than conflated". A generic
    `expected_profit` on the wire would force a client to guess which one it
    holds, which is the conflation itself.
    """
    schemas = create_app().openapi()["components"]["schemas"]
    company = schemas["CompanyEconomicsResponse"]["properties"]

    assert "incremental_grading_decision" in company
    assert "investment_return" in company
    assert "expected_profit" not in company


def test_openapi_documents_two_ratios_and_never_one_called_roi() -> None:
    """ADR 0007: two ROIs, never one. A single headline number is a new ADR."""
    schemas = create_app().openapi()["components"]["schemas"]
    company = schemas["CompanyEconomicsResponse"]["properties"]

    assert "incremental_roi" in company
    assert "investment_roi" in company
    assert "roi" not in company


def test_openapi_documents_no_cost_total() -> None:
    """#58: §46's costs are named line items, and nothing computes a grand total."""
    schemas = create_app().openapi()["components"]["schemas"]

    for name in ("CostConfigurationRequest", "CostConfigurationResponse"):
        assert not [field for field in schemas[name]["properties"] if "total" in field]


def test_the_configuration_endpoint_is_rate_limited_and_the_results_are_not() -> None:
    """ADR 0005: §55 names the analysis writes. Polling a result is not one of them."""
    paths = create_app().openapi()["paths"]

    assert "429" in paths["/analyses/{analysis_id}/economic-configuration"]["post"]["responses"]
    assert "429" not in paths["/analyses/{analysis_id}/results"]["get"]["responses"]


# ---------------------------------------------------------------------------
# The condition assessment — spec §6, §4 (#245)
# ---------------------------------------------------------------------------
COORDINATES = frozenset({"bounding_box", "polygon", "x", "y", "width", "height"})


def _references(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        if "$ref" in node:
            yield str(node["$ref"]).rsplit("/", 1)[-1]
        for value in node.values():
            yield from _references(value)
    elif isinstance(node, list):
        for item in node:
            yield from _references(item)


def test_openapi_serves_the_condition_without_a_coordinate() -> None:
    """Spec §4 excludes defect visualization; #175 forbids projecting a frame.

    Walked from `ConditionResponse` through every schema it references, so a
    coordinate smuggled in three levels down is caught as surely as one at the top.
    """
    schemas = create_app().openapi()["components"]["schemas"]
    assert "condition" in schemas["ResultsResponse"]["properties"]

    seen: set[str] = set()
    pending = ["ConditionResponse"]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        assert not COORDINATES & set(schemas[name].get("properties", {})), name
        pending.extend(_references(schemas[name]))

    assert len(seen) > 1
