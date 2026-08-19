"""Read the canonical card catalog from TCGdex.

`docs/adr/0004-the-canonical-card-catalog-source.md` selects TCGdex, MIT, read
through `api.tcgdex.net` or a self-hosted instance of the same MIT server.
This module is the adapter that reads it, and the **only** module in this
service that imports an HTTP client. Nothing on the request path may import it;
`test_import_purity.py` enforces that.

Three facts about the source shape everything below, and all three were measured
rather than assumed:

* **Rarity and printing variants come only from the per-card endpoint.** The set
  endpoint lists briefs — id, number, name. A full import is therefore about
  36,000 requests across English and Japanese, which is why fetching is a phase
  of its own that ends in a snapshot on disk rather than a phase that writes to
  PostgreSQL as it goes.
* **`variants_detailed` is what makes a printing economically legible.** The
  older boolean `variants` are independent flags: a Base Set Charizard that
  exists as 1st-edition-holo and as shadowless-holo sets `firstEdition` and
  `holo`, from which no reader can recover that there is no plain "1st edition"
  printing. `variants_detailed` names each printing outright, so it is read
  first and the flags are only a fallback.
* **A card payload carries `pricing` and `image`.** Prices are M4 and artwork is
  excluded from V1 by ADR 0004 — TCGdex's MIT licence covers its compilation,
  not The Pokémon Company's artwork. Both are dropped here, at the boundary, and
  `test_catalog_tcgdex.py` asserts neither reaches `metadata`.

The source publishes no version stamp of its own, so an import records the
`tcgdex/cards-database` HEAD commit when it can reach GitHub for it, and always
records the digest of the snapshot it wrote. The digest is the honest handle:
it describes the bytes this run actually loaded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx
from tcg_domain.catalog import Card, CardExternalId, Set

from tcg_api.catalog.snapshot import CatalogRecords, card_id, reject_duplicate_cards, set_id
from tcg_api.version import application_version

__all__ = [
    "CACHEABLE",
    "DEFAULT_API_BASE_URL",
    "DEFAULT_CONCURRENCY",
    "GAME",
    "LICENSE",
    "PROVIDER",
    "UPSTREAM_REPOSITORY",
    "CatalogImportError",
    "Fetched",
    "RetryPolicy",
    "card_number",
    "card_records",
    "create_client",
    "fetch",
    "resolve_revision",
    "set_record",
    "variant_slugs",
]

logger = logging.getLogger(__name__)

#: The provider key imported identifiers are recorded under (ADR 0004).
PROVIDER: Final = "tcgdex"

#: The licence the compilation arrives under, recorded on every version.
LICENSE: Final = "MIT"

#: TCGdex is a Pokémon database. The domain stays TCG-agnostic — this is a fact
#: about *this source*, not about the catalog, which is why it lives here.
GAME: Final = "pokemon"

DEFAULT_API_BASE_URL: Final = "https://api.tcgdex.net/v2"

UPSTREAM_REPOSITORY: Final = "https://github.com/tcgdex/cards-database"

_REVISION_URL: Final = "https://api.github.com/repos/tcgdex/cards-database/commits/master"

#: Polite by default. The source publishes no rate limit, has no SLA, and is run
#: for free; eight in flight finishes a full import in a reasonable time without
#: behaving like a scraper.
DEFAULT_CONCURRENCY: Final = 8

#: Which resources may be served from the on-disk cache. Cards only: a card is
#: immutable enough that refetching it after a failed run is waste, whereas the
#: set listing is exactly what changes when a set is completed upstream, and a
#: cached listing would pin a resumed run to a stale set of cards.
CACHEABLE: Final = ("cards",)

_RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})

#: A `Retry-After` longer than this is treated as a refusal rather than a wait.
_MAX_HONOURED_RETRY_AFTER: Final = 60.0

_NOT_A_SLUG_CHARACTER: Final = re.compile(r"[^a-z0-9]+")

#: Order matters: it is the order the slugs come out in, and it puts the
#: distinguishing part of a printing first.
_VARIANT_FLAGS: Final = (
    ("normal", "normal"),
    ("holo", "holo"),
    ("reverse", "reverse"),
    ("firstEdition", "1st-edition"),
    ("wPromo", "w-promo"),
)

#: A jumbo card is a different physical object with a different grading process,
#: so its size belongs in the variant. "standard" is the assumption and says
#: nothing, so it is left out.
_ASSUMED_SIZE: Final = "standard"

_jitter: Final = random.SystemRandom()


class CatalogImportError(RuntimeError):
    """The source could not be read.

    Deliberately not named `ImportError`, which is a builtin about Python
    modules. This is a CLI's failure, not a request's, so it carries no spec §66
    code: the taxonomy describes what an HTTP client is told, and nobody is
    being told anything here but an operator reading a log.
    """


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How hard to try before giving up on the source."""

    attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0

    def delay(self, attempt: int) -> float:
        """Exponential backoff with jitter, so eight workers do not retry in lockstep."""
        ceiling = min(self.base_delay * 2**attempt, self.max_delay)
        return ceiling * (0.5 + _jitter.random() / 2) if ceiling else 0.0


DEFAULT_RETRIES: Final = RetryPolicy()


@dataclass(frozen=True, slots=True)
class Fetched:
    """What one fetch produced, and what it could not."""

    records: CatalogRecords
    #: Cards the source listed in a set but then could not serve. Counted rather
    #: than raised: ADR 0004 warns that per-card completeness varies, and one
    #: gap must not cost the other twenty-three thousand.
    skipped: int


# ---------------------------------------------------------------------------
# Mapping — pure, and tested without a network
# ---------------------------------------------------------------------------
def _slugify(value: str) -> str:
    return _NOT_A_SLUG_CHARACTER.sub("-", value.lower()).strip("-")


def _without_none(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def set_record(payload: Mapping[str, Any], language: str) -> Set:
    """One TCGdex set as a catalog `Set`.

    `set_code` is the *printed* abbreviation where the source publishes one, and
    TCGdex's own key otherwise. That rule reproduces every hand-authored seed
    code exactly: `base1` is printed `BS`, `sv03.5` is printed `MEW`, and the
    Japanese sets carry no abbreviation because their TCGdex key already is the
    printed code (`SV2a`).
    """
    tcgdex_id = _require(payload, "id")
    abbreviation = payload.get("abbreviation") or {}
    released = payload.get("releaseDate")

    return Set(
        id=set_id(GAME, language, abbreviation.get("official") or tcgdex_id),
        game=GAME,
        language=language,
        set_code=abbreviation.get("official") or tcgdex_id,
        name=_require(payload, "name"),
        release_date=date.fromisoformat(released) if released else None,
        # No `logo` and no `symbol`: ADR 0004 excludes provider artwork from V1.
        metadata=_without_none(
            {
                "tcgdex_id": tcgdex_id,
                "serie": payload.get("serie"),
                "card_count": payload.get("cardCount"),
            }
        ),
    )


def card_number(local_id: str, card_count: Mapping[str, Any] | None) -> str:
    """The number as printed — `4/102`, `025/165`.

    `local_id` is used verbatim: TCGdex already supplies the padding the card
    carries, and inventing or stripping zeros would misquote the card. The
    denominator is the *official* count, which is what is printed; `total`
    includes secret rares numbered past it.
    """
    official = (card_count or {}).get("official")
    return f"{local_id}/{official}" if official else local_id


def variant_slugs(payload: Mapping[str, Any]) -> list[str | None]:
    """Every printing this card exists as, in source order.

    One slug per printing, built from the parts that distinguish it:
    stamps first, then subtype, foil and finish, then size when it is not the
    standard one. `1st-edition-shadowless-holo` and `shadowless-holo` are two
    printings of Base Set Charizard that trade very differently, and the whole
    point of this function is that they end up as two rows.

    Returns `[None]` — one row, no variant — when the source records no
    printings at all, rather than dropping the card.
    """
    slugs: list[str | None] = []
    for descriptor in payload.get("variants_detailed") or []:
        slug = _variant_slug(descriptor)
        if slug and slug not in slugs:
            slugs.append(slug)
    if slugs:
        return slugs

    flags = payload.get("variants") or {}
    slugs = [slug for key, slug in _VARIANT_FLAGS if flags.get(key)]
    return slugs or [None]


def _variant_slug(descriptor: Mapping[str, Any]) -> str:
    stamps = descriptor.get("stamp") or []
    size = descriptor.get("size")
    parts = [
        *stamps,
        descriptor.get("subtype"),
        descriptor.get("foil"),
        descriptor.get("type"),
        size if size and size != _ASSUMED_SIZE else None,
    ]
    return "-".join(_slugify(str(part)) for part in parts if part)


def card_records(
    payload: Mapping[str, Any], parent: Set
) -> tuple[list[Card], list[CardExternalId]]:
    """One TCGdex card as the rows it actually is — one per printing.

    Every row carries the *same* TCGdex identifier, because TCGdex issues one
    identifier for a card and this catalog holds a row per printing. #23 left
    `ix_card_external_ids_provider_external_id` deliberately non-unique so that
    importing a source shaped this way stays a data question rather than a
    schema change.
    """
    tcgdex_id = _require(payload, "id")
    number = card_number(_require(payload, "localId"), payload.get("set", {}).get("cardCount"))
    name = _require(payload, "name")
    rarity = payload.get("rarity")
    detailed = payload.get("variants_detailed") or []

    cards: list[Card] = []
    external_ids: list[CardExternalId] = []
    for position, variant in enumerate(variant_slugs(payload)):
        descriptor = detailed[position] if position < len(detailed) else {}
        card = Card(
            id=card_id(parent.game, parent.language, parent.set_code, number, variant),
            set=parent,
            card_number=number,
            name=name,
            rarity=rarity,
            variant=variant,
            # Identity and provenance only. Play data (hp, attacks, weaknesses)
            # has no bearing on grading or economics and is not carried.
            metadata=_without_none(
                {
                    "tcgdex_id": tcgdex_id,
                    "category": payload.get("category"),
                    "illustrator": payload.get("illustrator"),
                    "dex_id": payload.get("dexId"),
                    "regulation_mark": payload.get("regulationMark"),
                }
            ),
        )
        cards.append(card)
        external_ids.append(
            CardExternalId(
                card_id=card.id,
                provider=PROVIDER,
                external_id=tcgdex_id,
                # Which printing this shared identifier meant, in the source's
                # own terms — the only way back from a row to the descriptor
                # that produced it.
                metadata=_without_none({"variant_id": descriptor.get("variantId")}),
            )
        )

    return cards, external_ids


def _require(payload: Mapping[str, Any], field: str) -> Any:
    if field not in payload:
        raise CatalogImportError(f"the source returned a record with no {field!r}: {payload!r}")
    return payload[field]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def create_client(base_url: str) -> httpx.AsyncClient:
    """A client that identifies itself.

    The source is free, unmetered and run by volunteers; an anonymous flood of
    36,000 requests is the kind of thing that gets a project blocked. The agent
    string names the product and where to complain about it.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                f"tcg-grading-advisor/{application_version()} "
                "(+https://github.com/chuanseng-ng/tcg-analyzer)"
            ),
        },
    )


async def resolve_revision(client: httpx.AsyncClient | None = None) -> str | None:
    """The upstream commit this import corresponds to, or None.

    ADR 0004 asks that the provenance record carry the repository commit
    imported, and the REST API publishes no version of its own. This is the
    closest honest answer: the head of `tcgdex/cards-database` at the moment of
    the fetch. It is best-effort by design — an unreachable or rate-limited
    GitHub must not cost an import that otherwise succeeded, and a `NULL`
    revision beside a recorded snapshot digest says less but says it truthfully.
    """
    owned = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        response = await client.get(
            _REVISION_URL, headers={"Accept": "application/vnd.github+json"}
        )
        response.raise_for_status()
        revision = response.json().get("sha")
        return str(revision) if revision else None
    except (httpx.HTTPError, ValueError, KeyError) as error:
        logger.warning(
            "could not resolve the upstream revision from %s (%s); "
            "the version will record no source_revision",
            UPSTREAM_REPOSITORY,
            error,
        )
        return None
    finally:
        if owned:
            await client.aclose()


class _Source:
    """One fetch's client, budget and cache, so the helpers below take four fewer arguments."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        concurrency: int,
        retries: RetryPolicy,
        cache: Path | None,
    ) -> None:
        self._client = client
        self._gate = asyncio.Semaphore(concurrency)
        self._retries = retries
        self._cache = cache

    async def get(self, path: str, *, kind: str) -> Any:
        """Read a resource the source is expected to have."""
        payload = await self._load(path, kind=kind, missing_ok=False)
        if payload is None:  # pragma: no cover - `_load` raises rather than answering None
            raise CatalogImportError(f"GET {path} answered nothing")
        return payload

    async def get_if_present(self, path: str, *, kind: str) -> Any | None:
        """Read a resource the source listed but may not be able to serve."""
        return await self._load(path, kind=kind, missing_ok=True)

    async def _load(self, path: str, *, kind: str, missing_ok: bool) -> Any | None:
        cached = self._read_cache(kind, path)
        if cached is not None:
            return cached
        payload = await self._request(path, missing_ok=missing_ok)
        if payload is not None:
            self._write_cache(kind, path, payload)
        return payload

    async def _request(self, path: str, *, missing_ok: bool) -> Any | None:
        last = ""
        for attempt in range(self._retries.attempts):
            async with self._gate:
                try:
                    response = await self._client.get(path)
                except httpx.HTTPError as error:  # timeouts, resets, DNS
                    last = f"{type(error).__name__}: {error}"
                else:
                    if response.status_code == 404 and missing_ok:
                        return None
                    if response.status_code not in _RETRYABLE_STATUS:
                        if response.is_success:
                            return response.json()
                        raise CatalogImportError(f"GET {path} answered {response.status_code}")
                    last = f"HTTP {response.status_code}"
                    wait = _retry_after(response)
                    if wait is not None:
                        await asyncio.sleep(wait)
                        continue
            await asyncio.sleep(self._retries.delay(attempt))

        raise CatalogImportError(
            f"GET {path} kept failing after {self._retries.attempts} attempts ({last}). "
            "The source is unreachable or refusing; lower --concurrency or try later."
        )

    def _cache_path(self, kind: str, path: str) -> Path | None:
        if self._cache is None or kind not in CACHEABLE:
            return None
        return self._cache / f"{quote(path.strip('/'), safe='')}.json"

    def _read_cache(self, kind: str, path: str) -> Any | None:
        target = self._cache_path(kind, path)
        if target is None or not target.is_file():
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A half-written cache entry is not a reason to fail an import; the
            # source is still there.
            return None

    def _write_cache(self, kind: str, path: str, payload: Any) -> None:
        target = self._cache_path(kind, path)
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _retry_after(response: httpx.Response) -> float | None:
    """The wait the server asked for, when it asked in seconds and asked for a sane one."""
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        wait = float(header)
    except ValueError:
        return None  # an HTTP-date; fall back to our own backoff
    return wait if 0 <= wait <= _MAX_HONOURED_RETRY_AFTER else None


async def fetch(
    client: httpx.AsyncClient,
    languages: Sequence[str],
    *,
    sets: Sequence[str] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    retries: RetryPolicy = DEFAULT_RETRIES,
    cache: Path | None = None,
) -> Fetched:
    """Read `languages` (and optionally only `sets`) into catalog records.

    Sets are read one at a time and their cards in parallel: it keeps memory
    bounded across 400 sets, makes progress legible in the log, and means an
    interrupted run has a warm cache for everything it already read.

    **A set id belongs to one language.** `SV2a` is a Japanese set and `base1`
    an English one; there is no English `SV2a` to fetch. So `sets` is read as
    "import these, in whichever of `languages` has them", and a requested set
    found in none of them is an error naming it — a typo must not import
    nothing and report success.
    """
    source = _Source(client, concurrency=concurrency, retries=retries, cache=cache)
    requested = list(sets) if sets else None

    all_sets: list[Set] = []
    all_cards: list[Card] = []
    all_external_ids: list[CardExternalId] = []
    found: set[str] = set()
    skipped = 0

    for language in languages:
        for tcgdex_id in await _set_ids(source, language, requested):
            payload = await source.get_if_present(f"/{language}/sets/{tcgdex_id}", kind="sets")
            if payload is None:
                logger.debug("no %s set %s", language, tcgdex_id)
                continue
            found.add(tcgdex_id)
            record = set_record(payload, language)
            all_sets.append(record)

            briefs = payload.get("cards") or []
            fetched = await asyncio.gather(
                *(
                    source.get_if_present(f"/{language}/cards/{brief['id']}", kind="cards")
                    for brief in briefs
                )
            )
            for card_payload in fetched:
                if card_payload is None:
                    skipped += 1
                    continue
                cards, external_ids = card_records(card_payload, record)
                all_cards.extend(cards)
                all_external_ids.extend(external_ids)

            logger.info(
                "read %s/%s: %d cards so far, %d skipped",
                language,
                tcgdex_id,
                len(all_cards),
                skipped,
            )

    if requested and (missing := [name for name in requested if name not in found]):
        raise CatalogImportError(
            f"no set {', '.join(missing)} in {', '.join(languages)}. A TCGdex set id "
            "belongs to one language — `base1` is English, `SV2a` is Japanese."
        )

    reject_duplicate_cards(all_cards)
    return Fetched(
        records=CatalogRecords(
            sets=tuple(all_sets), cards=tuple(all_cards), external_ids=tuple(all_external_ids)
        ),
        skipped=skipped,
    )


async def _set_ids(
    source: _Source, language: str, requested: Sequence[str] | None
) -> Iterable[str]:
    if requested:
        return requested
    listing = await source.get(f"/{language}/sets", kind="set-listing")
    return [entry["id"] for entry in listing]
