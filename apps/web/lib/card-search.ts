/**
 * The catalog search query, in the three forms it has to take.
 *
 * The browser URL is this feature's state: `/cards?text=charizard&offset=20` is
 * the whole of what the page is showing, so a search survives a reload, a back
 * button and being pasted to someone else. That makes three representations
 * unavoidable — the URL's, the form's, and the API's — and this module is where
 * all three meet, so that no component has to know two of them at once.
 *
 * Nothing here touches React or `fetch`; it is ordinary functions over strings,
 * which is what makes the awkward parts testable on their own.
 */

import type { CardSearchParams } from "./api";

/**
 * Window size for one page of results. `GET /cards/search` defaults to this
 * value, so it never travels in the URL: a page size the user cannot change is
 * not state worth carrying, and leaving it out keeps a shared link short.
 */
export const PAGE_SIZE = 20;

/**
 * `MAX_SEARCH_OFFSET` from `services/api/src/tcg_api/routers/cards.py`.
 *
 * The route bounds it because a value past `bigint` overflows inside the driver
 * and surfaces as a 503 blaming the catalog. Clamping here as well means a
 * hand-edited URL cannot manufacture that lie.
 */
export const MAX_OFFSET = 1_000_000;

/**
 * A search as the form holds it.
 *
 * Filters are empty strings rather than `undefined` so each one can back a
 * controlled input directly. Empty means "not filtering on this" — it is never
 * sent, because `?text=` with an empty value is a 422 from the route's
 * validation, not an unfiltered search.
 */
export interface CardSearchQuery {
  /** A fragment of the printed name. Case-insensitive, and matches Japanese. */
  readonly text: string;
  /** What is printed on the card. Matched as a prefix, so `25` finds `025/165`. */
  readonly cardNumber: string;
  /** The publisher's set identifier, as printed — `BS`, `MEW`, `SV2a`. */
  readonly setCode: string;
  /** An ISO 639-1 code. `en` or `ja` in V1; empty means either. */
  readonly language: string;
  /** How many matches to skip. */
  readonly offset: number;
}

export const EMPTY_QUERY: CardSearchQuery = {
  text: "",
  cardNumber: "",
  setCode: "",
  language: "",
  offset: 0,
};

/** URL parameter names, kept identical to the API's so a link reads the same. */
const TEXT = "text";
const CARD_NUMBER = "card_number";
const SET_CODE = "set_code";
const LANGUAGE = "language";
const OFFSET = "offset";

/** The route validates `language` as ISO 639-1; anything else is a 422. */
const LANGUAGE_PATTERN = /^[a-z]{2}$/;

/** Whatever `useSearchParams` returns, without importing React here. */
export interface ReadonlySearchParams {
  get(name: string): string | null;
}

function readFilter(params: ReadonlySearchParams, name: string): string {
  return params.get(name)?.trim() ?? "";
}

function readOffset(params: ReadonlySearchParams): number {
  const raw = params.get(OFFSET);
  if (raw === null) {
    return 0;
  }

  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 0;
  }

  return Math.min(parsed, MAX_OFFSET);
}

/**
 * Read a query out of the browser URL.
 *
 * Tolerant by design: the URL is user-editable, and a malformed parameter
 * should narrow to something answerable rather than become a 422 the user
 * cannot act on. A padded value is trimmed, an unparseable offset becomes the
 * first page, and a `language` that is not an ISO 639-1 code is dropped rather
 * than forwarded for the route to reject.
 */
export function parseSearchQuery(params: ReadonlySearchParams): CardSearchQuery {
  const language = readFilter(params, LANGUAGE).toLowerCase();

  return {
    text: readFilter(params, TEXT),
    cardNumber: readFilter(params, CARD_NUMBER),
    setCode: readFilter(params, SET_CODE),
    language: LANGUAGE_PATTERN.test(language) ? language : "",
    offset: readOffset(params),
  };
}

/**
 * The URL form of a query: every blank filter left out, and `offset` only once
 * it is off the first page, so the common link is just `/cards?text=…`.
 */
export function toUrlSearchParams(query: CardSearchQuery): URLSearchParams {
  const params = new URLSearchParams();

  const filters: ReadonlyArray<readonly [string, string]> = [
    [TEXT, query.text],
    [CARD_NUMBER, query.cardNumber],
    [SET_CODE, query.setCode],
    [LANGUAGE, query.language],
  ];

  for (const [name, value] of filters) {
    const trimmed = value.trim();
    if (trimmed !== "") {
      params.set(name, trimmed);
    }
  }

  if (query.offset > 0) {
    params.set(OFFSET, String(Math.min(query.offset, MAX_OFFSET)));
  }

  return params;
}

/** A link to this search. `/cards` when nothing is filtered. */
export function cardsHref(query: CardSearchQuery): string {
  const params = toUrlSearchParams(query).toString();
  return params === "" ? "/cards" : `/cards?${params}`;
}

/**
 * The API form. `lib/api.ts` drops blanks again on the way out — this maps
 * names and pins the window; that module owns the wire rule.
 */
export function toApiParams(query: CardSearchQuery): CardSearchParams {
  return {
    text: query.text,
    card_number: query.cardNumber,
    set_code: query.setCode,
    language: query.language,
    limit: PAGE_SIZE,
    offset: Math.min(Math.max(query.offset, 0), MAX_OFFSET),
  };
}

/**
 * Whether the user has narrowed anything.
 *
 * This separates the two ways a page can come back with no rows: an empty
 * catalog, and filters that matched nothing. Only the second is worth telling
 * the user how to undo.
 */
export function hasFilters(query: CardSearchQuery): boolean {
  return (
    query.text !== "" || query.cardNumber !== "" || query.setCode !== "" || query.language !== ""
  );
}

/**
 * The human position of a page within the whole result set — the `21` and `40`
 * of "Showing 21–40 of 43".
 *
 * Counted from the rows actually returned rather than from `PAGE_SIZE`, so the
 * last page reports its real length, and an offset past the end reports
 * nothing rather than a range that does not exist.
 */
export function pageRange(offset: number, returned: number): { first: number; last: number } {
  if (returned <= 0) {
    return { first: 0, last: 0 };
  }
  return { first: offset + 1, last: offset + returned };
}
