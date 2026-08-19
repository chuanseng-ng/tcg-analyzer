"""The TCGdex adapter: what a payload becomes, and how it is fetched.

Every test here runs offline. The payloads under `fixtures/tcgdex/` were
recorded from `api.tcgdex.net` with `pricing` stripped — prices are M4, and
nothing in this repository holds them — and the two `sparse` files are
synthetic, carrying only what ADR 0004 says an import must tolerate.

The HTTP tests drive `httpx.MockTransport` rather than the network. One
`network`-marked test at the foot of this file does hit TCGdex; it is
deselected everywhere by default and exists so that an upstream change of shape
is discovered by a test rather than by an import that silently writes nonsense.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from tcg_api.catalog import tcgdex
from tcg_domain.catalog import Set

FIXTURES = Path(__file__).parent / "fixtures" / "tcgdex"

#: Retries are real but instantaneous, so a backoff test costs no wall clock.
FAST = tcgdex.RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0)


def payload(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    """`asyncio.run`, kept behind a helper so no test body is a coroutine.

    Same shape as `test_catalog_cards.py` — there is no pytest-asyncio here.
    """
    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------
def test_a_set_code_is_the_official_abbreviation() -> None:
    record = tcgdex.set_record(payload("set-en-base1.json"), "en")

    assert record.set_code == "BS"
    assert record.name == "Base Set"
    assert record.language == "en"
    assert record.game == "pokemon"
    assert record.release_date is not None
    assert record.release_date.isoformat() == "1999-01-09"


def test_a_set_code_falls_back_to_the_tcgdex_id_when_no_abbreviation_is_published() -> None:
    """Japanese sets carry no `abbreviation`, and `SV2a` is the printed code anyway."""
    record = tcgdex.set_record(payload("set-ja-SV2a.json"), "ja")

    assert record.set_code == "SV2a"
    assert record.name == "ポケモンカード151"
    assert record.language == "ja"


def test_the_abbreviation_is_preferred_over_the_tcgdex_id() -> None:
    """`sv03.5` is TCGdex's key; `MEW` is what is printed on the card."""
    record = tcgdex.set_record(payload("set-en-sv03.5.json"), "en")

    assert record.set_code == "MEW"
    assert record.name == "151"


def test_a_set_survives_having_neither_abbreviation_nor_release_date() -> None:
    record = tcgdex.set_record(payload("set-en-sparse.json"), "en")

    assert record.set_code == "sparse1"
    assert record.release_date is None


def test_set_metadata_carries_the_provider_key_and_no_artwork() -> None:
    record = tcgdex.set_record(payload("set-en-base1.json"), "en")

    assert record.metadata["tcgdex_id"] == "base1"
    assert record.metadata["serie"] == {"id": "base", "name": "Base"}
    assert "logo" not in record.metadata
    assert "symbol" not in record.metadata


# ---------------------------------------------------------------------------
# Card numbers
# ---------------------------------------------------------------------------
def test_a_card_number_is_the_local_id_over_the_official_count() -> None:
    assert tcgdex.card_number("4", {"official": 102, "total": 102}) == "4/102"


def test_a_local_id_keeps_its_own_padding() -> None:
    """`025/165` is what is printed; inventing or stripping zeros would misquote it."""
    assert tcgdex.card_number("025", {"official": 165, "total": 210}) == "025/165"


def test_a_card_number_is_the_local_id_alone_when_no_official_count_is_published() -> None:
    assert tcgdex.card_number("1", {"total": 3}) == "1"
    assert tcgdex.card_number("1", None) == "1"
    assert tcgdex.card_number("1", {"official": 0}) == "1"


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------
def test_variant_slugs_name_the_printing_and_not_merely_the_finish() -> None:
    """Base Set Charizard: four printings that trade at very different prices."""
    assert tcgdex.variant_slugs(payload("card-en-base1-4.json")) == [
        "unlimited-holo",
        "1st-edition-shadowless-holo",
        "shadowless-holo",
        "1999-2000-copyright-holo",
    ]


def test_a_stamp_leads_the_slug_and_a_subtype_follows_it() -> None:
    assert tcgdex.variant_slugs(payload("card-en-base1-58.json")) == [
        "unlimited-normal",
        "1st-edition-shadowless-normal",
        "shadowless-normal",
        "shadowless-red-cheek-normal",
        "1st-edition-shadowless-red-cheek-normal",
        "1999-2000-copyright-normal",
    ]


def test_a_foil_distinguishes_two_reverse_holos_of_the_same_card() -> None:
    """Pokéball and Masterball reverses are separate cards to a collector."""
    assert tcgdex.variant_slugs(payload("card-ja-SV2a-025.json")) == [
        "normal",
        "pokeball-reverse",
        "masterball-reverse",
    ]


def test_variants_fall_back_to_the_boolean_flags() -> None:
    """The flags cannot express a combination, so they are the second choice, not the first."""
    flags = {"firstEdition": True, "holo": True, "normal": False, "reverse": True, "wPromo": False}

    assert tcgdex.variant_slugs({"variants": flags}) == ["holo", "reverse", "1st-edition"]


def test_a_card_with_nothing_recorded_gets_one_row_and_no_variant() -> None:
    assert tcgdex.variant_slugs(payload("card-en-sparse.json")) == [None]
    assert tcgdex.variant_slugs({"variants_detailed": [], "variants": {}}) == [None]


def test_a_non_standard_size_is_part_of_the_slug() -> None:
    """A jumbo card is a different physical object and cannot be graded as the small one."""
    detailed = [{"type": "holo", "size": "jumbo"}, {"type": "holo", "size": "standard"}]

    assert tcgdex.variant_slugs({"variants_detailed": detailed}) == ["holo-jumbo", "holo"]


def test_repeated_descriptors_collapse_to_one_slug() -> None:
    """Two rows keyed the same would be one row after the upsert, and a silent loss."""
    detailed = [
        {"type": "holo", "variantId": "a"},
        {"type": "holo", "variantId": "b"},
        {"type": "reverse", "variantId": "c"},
    ]

    assert tcgdex.variant_slugs({"variants_detailed": detailed}) == ["holo", "reverse"]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def base_set() -> Set:
    return tcgdex.set_record(payload("set-en-base1.json"), "en")


def test_one_provider_identifier_names_every_printing_it_covers() -> None:
    """#23 left `(provider, external_id)` non-unique for exactly this."""
    cards, external_ids = tcgdex.card_records(payload("card-en-base1-4.json"), base_set())

    assert [card.variant for card in cards] == [
        "unlimited-holo",
        "1st-edition-shadowless-holo",
        "shadowless-holo",
        "1999-2000-copyright-holo",
    ]
    assert {card.card_number for card in cards} == {"4/102"}
    assert {external.external_id for external in external_ids} == {"base1-4"}
    assert {external.provider for external in external_ids} == {"tcgdex"}
    assert len(external_ids) == len(cards)
    assert len({external.card_id for external in external_ids}) == len(cards)


def test_each_row_records_which_printing_its_provider_identifier_meant() -> None:
    _, external_ids = tcgdex.card_records(payload("card-ja-SV2a-025.json"), base_set())

    assert [external.metadata["variant_id"] for external in external_ids] == [
        "endfynwn4n10gzq",
        "3739bbtj3i910y5ynn9xc6ryf",
        "2asus05yghmpd1ud1sdmlq3as4e",
    ]


def test_a_japanese_name_arrives_intact() -> None:
    cards, _ = tcgdex.card_records(payload("card-ja-SV2a-025.json"), base_set())

    assert {card.name for card in cards} == {"ピカチュウ"}


def test_a_rarity_of_none_is_a_rarity_and_not_a_missing_field() -> None:
    """`None` is a value in TCGdex's own rarity vocabulary: nothing is printed."""
    cards, _ = tcgdex.card_records(payload("card-ja-S12a-001.json"), base_set())

    assert {card.rarity for card in cards} == {"None"}


def test_a_card_missing_every_optional_field_still_imports() -> None:
    cards, external_ids = tcgdex.card_records(payload("card-en-sparse.json"), base_set())

    assert len(cards) == 1
    assert cards[0].rarity is None
    assert cards[0].variant is None
    assert cards[0].card_number == "1"
    assert len(external_ids) == 1


def test_no_price_or_artwork_reaches_the_catalog() -> None:
    """ADR 0004 excludes images from V1 and prices are M4. Neither may leak in via metadata."""
    forbidden = {"pricing", "image", "logo", "symbol", "images"}

    for name in sorted(path.name for path in FIXTURES.glob("card-*.json")):
        cards, external_ids = tcgdex.card_records(payload(name), base_set())
        for card in cards:
            assert forbidden.isdisjoint(_keys(dict(card.metadata))), name
        for external in external_ids:
            assert forbidden.isdisjoint(_keys(dict(external.metadata))), name


def _keys(node: object) -> set[str]:
    """Every key anywhere in a nested structure."""
    if isinstance(node, dict):
        found = set(node)
        for value in node.values():
            found |= _keys(value)
        return found
    if isinstance(node, list):
        return set().union(*(_keys(value) for value in node)) if node else set()
    return set()


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def stub(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=tcgdex.DEFAULT_API_BASE_URL
    )


def route(request: httpx.Request) -> httpx.Response:
    """Serve the recorded fixtures as though they were the API."""
    path = request.url.path
    if path.endswith("/sets/base1"):
        return httpx.Response(200, json=payload("set-en-base1.json"))
    if "/cards/" in path:
        card = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=payload(f"card-en-{card}.json"))
    raise AssertionError(f"unexpected request: {request.url}")


def test_a_fetch_reads_a_set_and_then_each_of_its_cards() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return route(request)

    async def scenario() -> tcgdex.Fetched:
        async with stub(handler) as client:
            return await tcgdex.fetch(client, ["en"], sets=["base1"], retries=FAST)

    fetched = run(scenario)

    assert seen[0].endswith("/en/sets/base1")
    assert len(seen) == 1 + 2  # the set, then the two cards the fixture lists
    assert [record.set_code for record in fetched.records.sets] == ["BS"]
    assert fetched.skipped == 0
    # Two cards, ten rows: Charizard has four printings and Pikachu six. A
    # per-card row count is the whole reason variants are read at all.
    assert len(fetched.records.cards) == 10
    assert len(fetched.records.external_ids) == 10


def test_a_card_the_source_cannot_serve_is_skipped_rather_than_fatal() -> None:
    """ADR 0004: per-card completeness varies. One gap must not lose the other 23,000."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("base1-58"):
            return httpx.Response(404, json={"status": 404})
        return route(request)

    async def scenario() -> tcgdex.Fetched:
        async with stub(handler) as client:
            return await tcgdex.fetch(client, ["en"], sets=["base1"], retries=FAST)

    fetched = run(scenario)

    assert fetched.skipped == 1
    assert {external.external_id for external in fetched.records.external_ids} == {"base1-4"}


def test_a_rate_limited_request_is_retried_after_the_interval_the_server_named() -> None:
    attempts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sets/base1") and len(attempts) < 2:
            attempts.append(0.0)
            return httpx.Response(429, headers={"Retry-After": "0"})
        return route(request)

    async def scenario() -> tcgdex.Fetched:
        async with stub(handler) as client:
            return await tcgdex.fetch(client, ["en"], sets=["base1"], retries=FAST)

    fetched = run(scenario)

    assert len(attempts) == 2
    assert [record.set_code for record in fetched.records.sets] == ["BS"]


def test_a_source_that_keeps_failing_stops_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def scenario() -> None:
        async with stub(handler) as client:
            await tcgdex.fetch(client, ["en"], sets=["base1"], retries=FAST)

    with pytest.raises(tcgdex.CatalogImportError, match="503"):
        run(scenario)


def test_never_more_requests_are_in_flight_than_the_concurrency_allows() -> None:
    live = 0
    peak = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            return route(request)
        finally:
            live -= 1

    async def scenario() -> None:
        async with stub(handler) as client:
            await tcgdex.fetch(client, ["en"], sets=["base1"], concurrency=2, retries=FAST)

    run(scenario)

    assert peak <= 2


def test_a_cached_card_is_not_fetched_twice(tmp_path: Path) -> None:
    """A run that dies at request 30,000 must not start over from one."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return route(request)

    async def scenario() -> None:
        async with stub(handler) as client:
            await tcgdex.fetch(client, ["en"], sets=["base1"], cache=tmp_path, retries=FAST)
            await tcgdex.fetch(client, ["en"], sets=["base1"], cache=tmp_path, retries=FAST)

    run(scenario)

    cards = [path for path in calls if "/cards/" in path]
    assert len(cards) == 2, "the second run refetched cards it had already cached"


def test_a_set_id_is_imported_from_whichever_language_has_it() -> None:
    """`base1` is English and `SV2a` is Japanese; neither exists in the other."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/ja/sets/base1":
            return httpx.Response(404, json={"status": 404})
        return route(request)

    async def scenario() -> tcgdex.Fetched:
        async with stub(handler) as client:
            return await tcgdex.fetch(client, ["ja", "en"], sets=["base1"], retries=FAST)

    fetched = run(scenario)

    assert [record.language for record in fetched.records.sets] == ["en"]


def test_a_set_found_in_no_language_is_named_rather_than_silently_skipped() -> None:
    """Otherwise a typo imports nothing and reports success."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/sets/" in request.url.path:
            return httpx.Response(404, json={"status": 404})
        return route(request)

    async def scenario() -> None:
        async with stub(handler) as client:
            await tcgdex.fetch(client, ["en"], sets=["bas1"], retries=FAST)

    with pytest.raises(tcgdex.CatalogImportError, match="bas1"):
        run(scenario)


def test_a_digital_only_serie_is_not_imported() -> None:
    """Pokémon TCG Pocket cards cannot be photographed, assessed or slabbed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sets/B2"):
            digital = payload("set-en-base1.json") | {
                "id": "B2",
                "name": "Fantastical Parade",
                "abbreviation": None,
                "serie": {"id": "tcgp", "name": "Pokémon TCG Pocket"},
            }
            return httpx.Response(200, json=digital)
        return route(request)

    async def scenario() -> tcgdex.Fetched:
        async with stub(handler) as client:
            return await tcgdex.fetch(client, ["en"], sets=["base1", "B2"], retries=FAST)

    fetched = run(scenario)

    assert [record.set_code for record in fetched.records.sets] == ["BS"]
    assert fetched.excluded == 1


def test_two_sets_claiming_one_set_code_name_each_other() -> None:
    """`uq_sets_game_language_set_code` would catch it, but only after an hour of fetching."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sets/base4"):
            rival = payload("set-en-base1.json") | {"id": "base4", "name": "Base Set 2"}
            return httpx.Response(200, json=rival)
        return route(request)

    async def scenario() -> None:
        async with stub(handler) as client:
            await tcgdex.fetch(client, ["en"], sets=["base1", "base4"], retries=FAST)

    with pytest.raises(tcgdex.CatalogImportError, match="Base Set 2"):
        run(scenario)


def test_the_set_listing_is_not_cached() -> None:
    """Which cards a set holds is what changes upstream; caching it would pin the run to a stale list."""
    assert tcgdex.CACHEABLE == ("cards",)


# ---------------------------------------------------------------------------
# The live source
# ---------------------------------------------------------------------------
@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="CI must not depend on a third-party service with no published SLA",
)
def test_the_live_source_still_has_the_shape_this_adapter_expects() -> None:
    async def scenario() -> tcgdex.Fetched:
        async with tcgdex.create_client(tcgdex.DEFAULT_API_BASE_URL) as client:
            return await tcgdex.fetch(client, ["en"], sets=["base1"])

    fetched = run(scenario)

    assert [record.set_code for record in fetched.records.sets] == ["BS"]
    charizard = [
        card
        for card in fetched.records.cards
        if card.card_number == "4/102" and card.name == "Charizard"
    ]
    assert {card.variant for card in charizard} >= {"unlimited-holo", "shadowless-holo"}
