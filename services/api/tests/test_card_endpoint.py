"""`GET /cards/{id}` — issue #29.

These tests run without PostgreSQL: the repository is a fake supplied through
`dependency_overrides`, which is what makes the failure paths — the catalog
unreachable, and an identifier naming no card — testable at all. The second is
reachable against a real database too, but only by guessing an identifier, and
the first is not reachable against a working one on purpose.

`test_catalog_cards.py` proves the real adapter answers the same questions
against real PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tcg_api.app import create_app
from tcg_api.config import get_settings
from tcg_api.database import get_engine, get_session_factory
from tcg_api.errors import ErrorCode
from tcg_api.routers.cards import card_repository
from tcg_api.storage import get_object_storage
from tcg_domain.catalog import Card, CardExternalId, CardId, Set, SetId
from tcg_domain.errors import CatalogUnavailable

CACHES = (get_settings, get_engine, get_session_factory, get_object_storage)

BASE_SET = Set(
    id=SetId(UUID("11111111-1111-5111-8111-111111111111")),
    game="pokemon",
    language="en",
    set_code="BS",
    name="Base Set",
    release_date=date(1999, 1, 9),
    metadata={"total_cards": 102},
)

CHARIZARD = Card(
    id=CardId(UUID("22222222-2222-5222-8222-222222222222")),
    set=BASE_SET,
    card_number="4/102",
    name="Charizard",
    rarity="Rare Holo",
    variant="unlimited-holo",
)

#: Two providers for one card. `card_external_ids` is deliberately not unique on
#: `(provider, external_id)` (#23), and this is the shape that proves the
#: response does not collapse them.
CHARIZARD_EXTERNAL_IDS = (
    CardExternalId(card_id=CHARIZARD.id, provider="example", external_id="example-base-charizard"),
    CardExternalId(card_id=CHARIZARD.id, provider="manual", external_id="bs-4-unlimited-holo"),
)

UNKNOWN = UUID("33333333-3333-5333-8333-333333333333")


class StubRepository:
    """Answers with whatever the test put in it. `search` is #28's, so it is absent."""

    def __init__(
        self,
        card: Card | None = None,
        external_ids: Sequence[CardExternalId] = (),
    ) -> None:
        self._card = card
        self._external_ids = external_ids

    async def get(self, card_id: CardId) -> Card | None:
        return self._card if self._card is not None and self._card.id == card_id else None

    async def external_ids(self, card_id: CardId) -> Sequence[CardExternalId]:
        return [external for external in self._external_ids if external.card_id == card_id]


class UnreachableRepository:
    """The adapter's promise when the driver fails: `CatalogUnavailable`, never asyncpg."""

    async def get(self, card_id: CardId) -> Card | None:
        raise CatalogUnavailable("The card catalog could not be reached.")

    async def external_ids(self, card_id: CardId) -> Sequence[CardExternalId]:
        raise CatalogUnavailable("The card catalog could not be reached.")


def app_with(repository: object) -> object:
    app = create_app()
    app.dependency_overrides[card_repository] = lambda: repository
    return app


def seeded() -> object:
    return app_with(StubRepository(CHARIZARD, CHARIZARD_EXTERNAL_IDS))


# ---------------------------------------------------------------------------
# The endpoint is mounted, and returns the canonical record
# ---------------------------------------------------------------------------
def test_the_card_endpoint_is_mounted_on_the_application() -> None:
    """A route that exists on the router but not on the app is a silent 404."""
    with TestClient(seeded()) as client:
        assert client.get(f"/cards/{CHARIZARD.id}").status_code == 200


def test_a_known_card_returns_its_full_detail() -> None:
    """Spec §6's Card block, asserted whole so a field cannot drift in silently."""
    with TestClient(seeded()) as client:
        response = client.get(f"/cards/{CHARIZARD.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": "22222222-2222-5222-8222-222222222222",
        "name": "Charizard",
        "card_number": "4/102",
        "game": "pokemon",
        "language": "en",
        "rarity": "Rare Holo",
        "variant": "unlimited-holo",
        "metadata": {},
        "set": {
            "id": "11111111-1111-5111-8111-111111111111",
            "set_code": "BS",
            "name": "Base Set",
            "release_date": "1999-01-09",
            "metadata": {"total_cards": 102},
        },
        "external_ids": [
            {"provider": "example", "external_id": "example-base-charizard"},
            {"provider": "manual", "external_id": "bs-4-unlimited-holo"},
        ],
    }


def test_one_card_may_carry_several_providers_identifiers() -> None:
    """§10's third table is what keeps a catalog source replaceable.

    The reverse lookup is deliberately not unique (#23), so the response must
    not collapse two providers into one.
    """
    with TestClient(seeded()) as client:
        body = client.get(f"/cards/{CHARIZARD.id}").json()

    assert [entry["provider"] for entry in body["external_ids"]] == ["example", "manual"]


def test_no_card_images_are_offered() -> None:
    """ADR 0004 imports none, so a field for one could only ever be empty."""
    with TestClient(seeded()) as client:
        body = client.get(f"/cards/{CHARIZARD.id}").json()

    assert "image_front" not in body
    assert "image_back" not in body


def test_identification_confidence_is_not_a_property_of_a_catalog_card() -> None:
    """It belongs to an analysis. Publishing it here invites a client to misread one."""
    with TestClient(seeded()) as client:
        body = client.get(f"/cards/{CHARIZARD.id}").json()

    assert "identification_confidence" not in body
    assert "confidence" not in body


# ---------------------------------------------------------------------------
# The 404, inside spec §66's taxonomy
# ---------------------------------------------------------------------------
def test_an_unknown_identifier_is_a_404_in_the_taxonomys_envelope() -> None:
    with TestClient(seeded()) as client:
        response = client.get(f"/cards/{UNKNOWN}")

    assert response.status_code == 404
    assert response.json() == {
        "code": "card_not_identified",
        "message": "No card is recorded under that identifier.",
        "details": {"card_id": str(UNKNOWN)},
    }


def test_no_ninth_error_code_was_invented() -> None:
    """Spec §66 names eight codes and no `not_found`.

    Adding one is a specification change rather than a local decision — the same
    reasoning that made `/catalog/version` answer 503 rather than invent one.
    """
    with TestClient(seeded()) as client:
        code = client.get(f"/cards/{UNKNOWN}").json()["code"]

    assert code in {member.value for member in ErrorCode}


def test_a_malformed_identifier_is_a_422_and_not_a_500() -> None:
    """`card_id` is typed `UUID`, so this is FastAPI's own request validation.

    `tcg_api.errors` deliberately leaves those alone: a malformed path segment
    is a transport-level failure with no §66 meaning. What must not happen is
    the catch-all turning it into `internal_error`.
    """
    with TestClient(seeded()) as client:
        response = client.get("/cards/not-a-uuid")

    assert response.status_code == 422


def test_an_unknown_card_costs_no_external_id_lookup() -> None:
    """The 404 path asks one question, not two.

    `external_ids` answers empty for an unknown card as well as for a card no
    source has claimed, so on this path it is a round trip that tells us nothing.
    """
    asked: list[str] = []

    class CountingRepository(StubRepository):
        async def get(self, card_id: CardId) -> Card | None:
            asked.append("get")
            return await super().get(card_id)

        async def external_ids(self, card_id: CardId) -> Sequence[CardExternalId]:
            asked.append("external_ids")
            return await super().external_ids(card_id)

    with TestClient(app_with(CountingRepository(CHARIZARD, CHARIZARD_EXTERNAL_IDS))) as client:
        client.get(f"/cards/{UNKNOWN}")

    assert asked == ["get"]


# ---------------------------------------------------------------------------
# The catalog being unreachable is not the card being absent
# ---------------------------------------------------------------------------
def test_an_unreachable_catalog_is_a_503_and_says_so() -> None:
    """Never a 404: nothing is known to be missing, only unread."""
    with TestClient(app_with(UnreachableRepository())) as client:
        response = client.get(f"/cards/{CHARIZARD.id}")

    assert response.status_code == 503
    assert response.json()["code"] == "provider_error"
    assert response.json()["details"] == {"reason": "catalog_unreachable"}


def test_no_connection_detail_reaches_the_client() -> None:
    """Spec §66: "do not expose raw stack traces to users"."""
    with TestClient(app_with(UnreachableRepository())) as client:
        body = client.get(f"/cards/{CHARIZARD.id}").text

    assert "postgresql" not in body.lower()
    assert "asyncpg" not in body.lower()
    assert "traceback" not in body.lower()


@pytest.fixture
def app_without_a_database(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TCG_API_DATABASE_URL", raising=False)
    for cached in CACHES:
        cached.cache_clear()
    yield create_app()
    for cached in CACHES:
        cached.cache_clear()


def test_the_card_endpoint_needs_a_database(app_without_a_database) -> None:
    """An unconfigured database is the same 503 as an unreachable one, not a 500."""
    with TestClient(app_without_a_database) as client:
        response = client.get(f"/cards/{uuid4()}")

    assert response.status_code == 503
    assert response.json()["details"] == {"reason": "catalog_unreachable"}


# ---------------------------------------------------------------------------
# The contract apps/web generates from
# ---------------------------------------------------------------------------
def test_the_endpoint_is_documented_with_a_response_model() -> None:
    """A bare dict would leave `apps/web` without a generated type — ADR 0001."""
    schema = create_app().openapi()
    operation = schema["paths"]["/cards/{card_id}"]["get"]

    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    model = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]

    assert set(model["properties"]) == {
        "id",
        "name",
        "card_number",
        "game",
        "language",
        "rarity",
        "variant",
        "metadata",
        "set",
        "external_ids",
    }


def test_the_nested_set_is_documented_as_its_own_model() -> None:
    """Nested rather than linked, and typed rather than an untyped object."""
    schemas = create_app().openapi()["components"]["schemas"]

    assert set(schemas["CardSetResponse"]["properties"]) == {
        "id",
        "set_code",
        "name",
        "release_date",
        "metadata",
    }


def test_the_failure_responses_are_documented() -> None:
    """The 404 and 503 are the endpoint's own; the 500 comes from `ERROR_RESPONSES`."""
    operation = create_app().openapi()["paths"]["/cards/{card_id}"]["get"]

    for status_code in ("404", "503", "500"):
        reference = operation["responses"][status_code]["content"]["application/json"]["schema"][
            "$ref"
        ]
        assert reference.endswith("/ErrorResponse"), status_code
