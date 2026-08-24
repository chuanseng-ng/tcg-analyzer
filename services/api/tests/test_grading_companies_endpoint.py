"""`GET /grading-companies` — issue #48.

Spec §24 requires BGS half grades, and the grade the three companies actually
disagree about is 9.5: PSA and TAG have none, BGS does. Everything else about
this endpoint exists so that a frontend never has to know that — it renders what
`grades` says, and a fourth company appears with no frontend change.

These tests run without PostgreSQL. `grading_rules_in_force` is the whole seam:
it is one dependency resolving every company's version, so a fake supplied
through `dependency_overrides` covers both the happy path and the 503 that a
working database never reaches.

`test_grading_schema.py` proves the resolver itself answers correctly against
real PostgreSQL.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.grading.rules import GradingRulesUnavailable
from tcg_api.routers import grading
from tcg_api.routers.grading import grading_rules_in_force
from tcg_api.storage import get_object_storage
from tcg_grading_companies import ADAPTERS, GradingRules

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

#: What the seeded table holds. Built here rather than imported from
#: `tcg_grading_companies` on purpose: the endpoint's contract is that the
#: version comes from the *database*, so a test reading the constants would pass
#: even if the route stopped querying at all.
PUBLISHED = {
    "psa": GradingRules(
        company="psa",
        version="psa-rules-2026-08-24",
        effective_from=date(2008, 2, 1),
        source="https://www.psacard.com/gradingstandards",
        verified_on=date(2026, 8, 24),
    ),
    "tag": GradingRules(
        company="tag",
        version="tag-rules-2026-08-24",
        effective_from=None,
        source="https://taggrading.com/pages/scale",
        verified_on=date(2026, 8, 24),
    ),
    "bgs": GradingRules(
        company="bgs",
        version="bgs-rules-2026-08-24",
        effective_from=None,
        source="https://www.beckett.com/grading/scale",
        verified_on=date(2026, 8, 24),
    ),
}

#: A company whose standard is superseded reports the date it stopped applying,
#: derived rather than stored — see #47.
SUPERSEDED = GradingRules(
    company="psa",
    version="psa-rules-2008",
    effective_from=date(2008, 2, 1),
    effective_to=date(2026, 8, 24),
    source="https://www.psacard.com/gradingstandards",
    verified_on=date(2026, 8, 24),
)


def app_with(rules: object) -> object:
    app = create_app()
    app.dependency_overrides[grading_rules_in_force] = lambda: rules
    return app


def companies(rules: object = PUBLISHED) -> dict[str, dict]:
    with TestClient(app_with(rules)) as client:
        response = client.get("/grading-companies")
    assert response.status_code == 200
    return {entry["company"]: entry for entry in response.json()["companies"]}


# ---------------------------------------------------------------------------
# Every supported company, in a stable order
# ---------------------------------------------------------------------------
def test_the_endpoint_is_mounted() -> None:
    with TestClient(app_with(PUBLISHED)) as client:
        response = client.get("/grading-companies")

    assert response.status_code == 200


def test_all_three_companies_are_returned_in_the_adapters_order() -> None:
    """Spec §48 lists PSA, TAG and BGS; `ADAPTERS` fixes the order once."""
    with TestClient(app_with(PUBLISHED)) as client:
        payload = client.get("/grading-companies").json()

    assert [entry["company"] for entry in payload["companies"]] == list(ADAPTERS)


@pytest.mark.parametrize(("slug", "expected"), [("psa", "PSA"), ("tag", "TAG"), ("bgs", "BGS")])
def test_each_company_carries_a_display_name(slug: str, expected: str) -> None:
    assert companies()[slug]["display_name"] == expected


# ---------------------------------------------------------------------------
# The scales — the reason the endpoint exists
# ---------------------------------------------------------------------------
def test_bgs_issues_nine_and_a_half_and_the_others_do_not() -> None:
    """The one grade the three companies disagree about (#46).

    The issue body's own note says "BGS uses half grades (8.5, 9.5); PSA and TAG
    do not", and it is wrong. A frontend written to it refuses a PSA 8.5.
    """
    scales = companies()

    assert "9.5" in scales["bgs"]["grades"]
    assert "9.5" not in scales["psa"]["grades"]
    assert "9.5" not in scales["tag"]["grades"]


@pytest.mark.parametrize("slug", list(ADAPTERS))
def test_every_company_issues_half_grades(slug: str) -> None:
    """All three do, 9.5 aside — which is the opposite of the common summary."""
    assert "8.5" in companies()[slug]["grades"]


@pytest.mark.parametrize(("slug", "expected"), [("psa", 18), ("tag", 18), ("bgs", 19)])
def test_each_scale_has_the_grades_the_company_publishes(slug: str, expected: int) -> None:
    grades = companies()[slug]["grades"]

    assert len(grades) == expected
    assert len(set(grades)) == expected


@pytest.mark.parametrize("slug", list(ADAPTERS))
def test_grades_ascend_from_one_to_ten(slug: str) -> None:
    """Ascending, so a client renders the list as it arrives.

    Sorted as *grades*, not as strings — `"10"` sorts before `"2"` lexically.
    """
    grades = companies()[slug]["grades"]

    assert grades[0] == "1"
    assert grades[-1] == "10"
    assert grades == sorted(grades, key=float)


@pytest.mark.parametrize("slug", list(ADAPTERS))
def test_no_scale_names_a_bucket(slug: str) -> None:
    """A scale names points. `7_or_lower` is a distribution key, not a grade."""
    assert not any("_or_" in grade for grade in companies()[slug]["grades"])


# ---------------------------------------------------------------------------
# The rules version — read from the database, not from the adapters
# ---------------------------------------------------------------------------
def test_each_company_reports_the_version_in_force() -> None:
    """Spec §23's identifier, which §57 records against an analysis."""
    assert companies()["psa"]["rules"]["version"] == "psa-rules-2026-08-24"


def test_the_reported_version_is_the_one_the_database_holds() -> None:
    """Not the adapter's constant — the two can differ once a successor is published."""
    published = dict(PUBLISHED, psa=SUPERSEDED)

    rules = companies(published)["psa"]["rules"]

    assert rules["version"] == "psa-rules-2008"
    assert rules["effective_to"] == "2026-08-24"


def test_a_stated_effective_date_is_reported_and_an_unstated_one_is_null() -> None:
    """PSA states 2008-02-01; TAG and BGS state none, and that is not a guess."""
    scales = companies()

    assert scales["psa"]["rules"]["effective_from"] == "2008-02-01"
    assert scales["tag"]["rules"]["effective_from"] is None


def test_a_company_with_no_published_version_reports_null_rules() -> None:
    """Nothing seeded is an honest `null`, never the adapter's constant.

    The scale is still there, so a picker renders even against an unseeded
    database.
    """
    entry = companies({**PUBLISHED, "bgs": None})["bgs"]

    assert entry["rules"] is None
    assert "9.5" in entry["grades"]


# ---------------------------------------------------------------------------
# Cacheable — the issue's own word for slow-moving reference data
# ---------------------------------------------------------------------------
def test_the_response_is_cacheable() -> None:
    with TestClient(app_with(PUBLISHED)) as client:
        response = client.get("/grading-companies")

    assert response.headers["Cache-Control"] == "public, max-age=3600"


# ---------------------------------------------------------------------------
# The failure path — 503, and a `details.reason` of its own
# ---------------------------------------------------------------------------
@pytest.fixture
def app_without_a_database(monkeypatch: pytest.MonkeyPatch):
    """The service running with no `TCG_API_DATABASE_URL` at all.

    Not a 500: a deployment that cannot reach its reference data is unavailable,
    and an unset URL is the same unavailability as a refused connection.
    """
    monkeypatch.delenv("TCG_API_DATABASE_URL", raising=False)
    for cached in CACHES:
        cached.cache_clear()
    yield create_app()
    for cached in CACHES:
        cached.cache_clear()


def test_an_unconfigured_database_is_a_503(app_without_a_database) -> None:
    with TestClient(app_without_a_database) as client:
        response = client.get("/grading-companies")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "provider_error"
    assert body["details"]["reason"] == "grading_rules_unreachable"


def test_a_failed_read_is_a_503_and_is_not_cached(app_without_a_database) -> None:
    """A refusal must never carry the success path's `Cache-Control`."""
    with TestClient(app_without_a_database) as client:
        response = client.get("/grading-companies")

    assert "Cache-Control" not in response.headers


def test_the_failure_reason_is_distinct_from_the_catalogs(app_without_a_database) -> None:
    """Four `details.reason` values already exist; this is the fifth.

    Folding it into `catalog_unreachable` would tell an operator to look at the
    wrong table.
    """
    with TestClient(app_without_a_database) as client:
        reason = client.get("/grading-companies").json()["details"]["reason"]
        catalog = client.get("/catalog/version").json()["details"]["reason"]

    assert reason != catalog


# ---------------------------------------------------------------------------
# The OpenAPI contract — `apps/web` generates its types from it (ADR 0001)
# ---------------------------------------------------------------------------
def test_openapi_documents_the_endpoint() -> None:
    schema = create_app().openapi()

    assert "get" in schema["paths"]["/grading-companies"]


def test_openapi_documents_the_response_model() -> None:
    """A bare list would leave `apps/web` without a generated type."""
    schema = create_app().openapi()

    operation = schema["paths"]["/grading-companies"]["get"]
    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    model = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]

    assert set(model["properties"]) == {"companies"}


def test_openapi_documents_every_field_a_client_renders_from() -> None:
    schemas = create_app().openapi()["components"]["schemas"]

    assert set(schemas["GradingCompanyResponse"]["properties"]) == {
        "company",
        "display_name",
        "grades",
        "rules",
    }
    assert set(schemas["GradingRulesResponse"]["properties"]) == {
        "version",
        "effective_from",
        "effective_to",
        "source",
        "verified_on",
    }


def test_openapi_documents_the_unavailable_response() -> None:
    operation = create_app().openapi()["paths"]["/grading-companies"]["get"]

    content = operation["responses"]["503"]["content"]["application/json"]
    assert content["schema"]["$ref"].endswith("/ErrorResponse")


def test_the_endpoint_is_not_rate_limited() -> None:
    """§55 names analysis endpoints and image uploads; reference data is neither.

    Same reading ADR 0005 applied to `GET /cards/search`.
    """
    operation = create_app().openapi()["paths"]["/grading-companies"]["get"]

    assert "429" not in operation["responses"]


# ---------------------------------------------------------------------------
# The other half of the 503: the database is configured, and the read fails
# ---------------------------------------------------------------------------
class _NoSession:
    """Stands in for a session factory whose sessions are never used.

    `rules_in_force` is patched to fail before it touches one, which is the
    point: what is under test is that a driver failure becomes a 503 rather than
    a 500 carrying an asyncpg message.
    """

    def __call__(self) -> _NoSession:
        return self

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_: object) -> bool:
        return False


def test_a_driver_failure_is_a_503_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fails(*_: object, **__: object) -> None:
        raise GradingRulesUnavailable("The grading rules could not be read.")

    monkeypatch.setattr(grading, "get_session_factory", _NoSession())
    monkeypatch.setattr(grading, "rules_in_force", fails)

    with TestClient(create_app()) as client:
        response = client.get("/grading-companies")

    assert response.status_code == 503
    assert response.json()["details"]["reason"] == "grading_rules_unreachable"
