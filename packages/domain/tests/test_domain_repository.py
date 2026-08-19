"""The `CardRepository` port — the query shapes #23 indexes and #28 exposes.

The in-memory repository below is a **test double, not shipped code**: the
domain package must not carry a fake catalog. It exists so the port's contract
is exercised rather than merely declared, and so `mypy --strict` proves a plain
class satisfies the Protocol structurally. #29's PostgreSQL adapter is the
first real implementation, and may promote this into a shared contract suite
the way `packages/shared/tests/test_storage_contract.py` does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import FrozenInstanceError, dataclass, field
from uuid import uuid4

import pytest
from tcg_domain import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    Card,
    CardExternalId,
    CardId,
    CardPage,
    CardQuery,
    CardRepository,
    Game,
    InvalidCardSearch,
    Language,
    Set,
    SetId,
)

BASE_SET = Set(
    id=SetId(uuid4()),
    game=Game.POKEMON,
    language=Language.ENGLISH,
    set_code="base1",
    name="Base Set",
)
JAPANESE_SET = Set(
    id=SetId(uuid4()),
    game=Game.POKEMON,
    language=Language.JAPANESE,
    set_code="SV3a",
    name="レイジングサーフ",
)


def a_card(set_: Set = BASE_SET, **overrides: object) -> Card:
    fields: dict[str, object] = {
        "id": CardId(uuid4()),
        "set": set_,
        "card_number": "4",
        "name": "Charizard",
    }
    fields.update(overrides)
    return Card(**fields)  # type: ignore[arg-type]


def run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())


# --------------------------------------------------------------------------
# A minimal implementation, to prove the port is satisfiable without a database
# --------------------------------------------------------------------------


@dataclass(slots=True)
class InMemoryCardRepository:
    cards: dict[CardId, Card] = field(default_factory=dict)
    external: list[CardExternalId] = field(default_factory=list)

    async def get(self, card_id: CardId) -> Card | None:
        return self.cards.get(card_id)

    async def search(
        self,
        query: CardQuery,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
    ) -> CardPage:
        matched = [card for card in self.cards.values() if _matches(query, card)]
        matched.sort(key=lambda card: (card.set.set_code, card.card_number, str(card.id)))
        window = matched[offset : offset + limit]
        return CardPage(cards=window, total=len(matched), limit=limit, offset=offset)

    async def external_ids(self, card_id: CardId) -> Sequence[CardExternalId]:
        return [external for external in self.external if external.card_id == card_id]


def _matches(query: CardQuery, card: Card) -> bool:
    return all(
        [
            query.text is None or query.text in card.name,
            query.game is None or query.game == card.game,
            query.language is None or query.language == card.language,
            query.set_code is None or query.set_code == card.set.set_code,
            query.card_number is None or card.card_number.startswith(query.card_number),
            query.variant is None or query.variant == card.variant,
        ]
    )


def a_repository(*cards: Card) -> InMemoryCardRepository:
    return InMemoryCardRepository(cards={card.id: card for card in cards})


def test_a_plain_class_satisfies_the_port() -> None:
    """Structural, not nominal — an adapter never subclasses the Protocol."""
    repository: CardRepository = a_repository()
    assert repository is not None


# --------------------------------------------------------------------------
# Lookup
# --------------------------------------------------------------------------


def test_a_known_card_is_returned() -> None:
    card = a_card()
    repository = a_repository(card)

    async def scenario() -> Card | None:
        return await repository.get(card.id)

    assert run(scenario) == card


def test_an_unknown_card_is_absent_rather_than_an_error() -> None:
    """Absence is an answer. #29 turns it into a 404; the port does not raise."""
    repository = a_repository()

    async def scenario() -> Card | None:
        return await repository.get(CardId(uuid4()))

    assert run(scenario) is None


def test_external_ids_are_reachable_for_one_card() -> None:
    card = a_card()
    repository = a_repository(card)
    repository.external.append(
        CardExternalId(card_id=card.id, provider="tcgdex", external_id="base1-4")
    )

    async def scenario() -> Sequence[CardExternalId]:
        return await repository.external_ids(card.id)

    assert [external.provider for external in run(scenario)] == ["tcgdex"]


# --------------------------------------------------------------------------
# `CardQuery` — the filters #23 must index and #28 must expose
# --------------------------------------------------------------------------


def test_an_empty_query_is_a_browse_of_everything() -> None:
    assert CardQuery() == CardQuery(
        text=None, game=None, language=None, set_code=None, card_number=None, variant=None
    )


def test_a_query_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        CardQuery().text = "Charizard"  # type: ignore[misc]


def test_searching_by_name_set_and_number() -> None:
    charizard = a_card(name="Charizard", card_number="4")
    blastoise = a_card(name="Blastoise", card_number="2")
    repository = a_repository(charizard, blastoise)

    async def scenario() -> tuple[CardPage, CardPage, CardPage]:
        return (
            await repository.search(CardQuery(text="Chariz")),
            await repository.search(CardQuery(set_code="base1")),
            await repository.search(CardQuery(card_number="2")),
        )

    by_name, by_set, by_number = run(scenario)
    assert [card.name for card in by_name.cards] == ["Charizard"]
    assert by_set.total == 2
    assert [card.name for card in by_number.cards] == ["Blastoise"]


def test_a_japanese_query_reaches_japanese_cards() -> None:
    japanese = a_card(JAPANESE_SET, name="リザードンex", card_number="125")
    repository = a_repository(japanese, a_card())

    async def scenario() -> CardPage:
        return await repository.search(CardQuery(text="リザードン", language=Language.JAPANESE))

    assert [card.name for card in run(scenario).cards] == ["リザードンex"]


@pytest.mark.parametrize("game", ["", "   ", "Pokemon", "pokemon_tcg"])
def test_a_query_game_must_be_a_lowercase_slug(game: str) -> None:
    with pytest.raises(InvalidCardSearch):
        CardQuery(game=game)


@pytest.mark.parametrize("language", ["", "EN", "eng", "en-US"])
def test_a_query_language_must_be_an_iso_639_1_code(language: str) -> None:
    with pytest.raises(InvalidCardSearch):
        CardQuery(language=language)


@pytest.mark.parametrize("value", ["", "   ", " base1", "base1 "])
@pytest.mark.parametrize("field_name", ["text", "set_code", "card_number", "variant"])
def test_a_query_filter_must_be_present_and_untrimmed(field_name: str, value: str) -> None:
    """An empty filter is a bug, not a wildcard — omit it instead."""
    with pytest.raises(InvalidCardSearch):
        CardQuery(**{field_name: value})


def test_a_partial_card_number_is_a_legitimate_filter() -> None:
    """Users type what is printed, which may be `025/102` or just `25`."""
    assert CardQuery(card_number="25").card_number == "25"


# --------------------------------------------------------------------------
# `CardPage` — pagination that cannot drop or duplicate a row
# --------------------------------------------------------------------------


def test_paging_covers_every_row_exactly_once() -> None:
    cards = [a_card(card_number=str(number), name=f"Card {number}") for number in range(1, 6)]
    repository = a_repository(*cards)

    async def scenario() -> list[CardPage]:
        return [
            await repository.search(CardQuery(), limit=2, offset=offset) for offset in (0, 2, 4)
        ]

    pages = run(scenario)
    seen = [card.id for page in pages for card in page.cards]
    assert len(seen) == len(set(seen)) == 5
    assert {page.total for page in pages} == {5}


def test_a_page_records_the_window_it_answered() -> None:
    page = CardPage(cards=(), total=0, limit=DEFAULT_SEARCH_LIMIT, offset=0)
    assert page.limit == DEFAULT_SEARCH_LIMIT
    assert page.offset == 0
    assert page.total == 0


def test_a_page_copies_its_cards() -> None:
    cards = [a_card()]
    page = CardPage(cards=cards, total=1, limit=DEFAULT_SEARCH_LIMIT, offset=0)
    cards.clear()
    assert len(page.cards) == 1


@pytest.mark.parametrize(
    ("total", "limit", "offset"),
    [
        (-1, DEFAULT_SEARCH_LIMIT, 0),
        (0, 0, 0),
        (0, MAX_SEARCH_LIMIT + 1, 0),
        (0, DEFAULT_SEARCH_LIMIT, -1),
    ],
)
def test_a_page_rejects_an_impossible_window(total: int, limit: int, offset: int) -> None:
    with pytest.raises(InvalidCardSearch):
        CardPage(cards=(), total=total, limit=limit, offset=offset)


def test_a_page_cannot_hold_more_cards_than_its_limit() -> None:
    with pytest.raises(InvalidCardSearch):
        CardPage(cards=(a_card(), a_card()), total=2, limit=1, offset=0)


def test_a_page_cannot_reach_past_the_total_it_reports() -> None:
    with pytest.raises(InvalidCardSearch):
        CardPage(cards=(a_card(),), total=1, limit=DEFAULT_SEARCH_LIMIT, offset=1)


def test_an_empty_page_may_sit_past_the_end_of_the_result_set() -> None:
    """Paging past the end is a question, and "nothing here" is its answer.

    A client asking for page 3 of a result set that has since shrunk to one page
    has done nothing wrong, and the window it describes is not incoherent — it
    skipped past everything and found nothing, which is exactly what it reports.
    The check above still guards what it was written to guard: a page holding
    cards cannot claim to sit beyond the matches it counted.
    """
    page = CardPage(cards=(), total=3, limit=DEFAULT_SEARCH_LIMIT, offset=99)

    assert page.cards == ()
    assert page.total == 3
    assert page.offset == 99


def test_the_search_limits_are_stated_for_the_api_to_enforce() -> None:
    assert 0 < DEFAULT_SEARCH_LIMIT <= MAX_SEARCH_LIMIT
