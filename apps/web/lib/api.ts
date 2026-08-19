/**
 * Client for the FastAPI service in `services/api`.
 *
 * Response types come from `./api-types`, generated from the service's OpenAPI
 * schema — see `docs/adr/0001-language-boundaries-in-the-monorepo.md`. The
 * schema is the frontend/backend contract, so do not hand-write a response
 * type here: add the endpoint to the schema and regenerate with
 * `pnpm --filter @tcg/web gen:api-types`. CI fails if the two drift apart.
 */

import type { components, operations } from "./api-types";
import { apiBaseUrl } from "./env";

/** Health checks are a liveness signal; a slow answer is a failed one. */
const HEALTH_TIMEOUT_MS = 5_000;

/**
 * Catalog reads get a longer deadline than health. A name search is a
 * deliberate sequential scan — `services/api/src/tcg_api/catalog/tables.py`
 * records that choice — so it is allowed to be slower than a liveness probe
 * without being taken for a service that is down.
 */
const CATALOG_TIMEOUT_MS = 10_000;

/**
 * `GET /health`.
 *
 * Aliased rather than re-declared so that a change to the server's model is a
 * compile error here, not a silent mismatch. `status` is narrower than it looks
 * — the schema pins it to the literal `"ok"`.
 */
export type HealthResponse = components["schemas"]["HealthResponse"];

/** `GET /cards/{card_id}` — the canonical record for one card. */
export type CardResponse = components["schemas"]["CardResponse"];

/**
 * One row of `GET /cards/search`. Deliberately narrower than
 * {@link CardResponse}: a search result carries no `metadata` and no
 * `external_ids`.
 */
export type CardSummaryResponse = components["schemas"]["CardSummaryResponse"];

/** The set a card belongs to, nested inside both card shapes. */
export type CardSetResponse = components["schemas"]["CardSetResponse"];

/** A provider's own identifier for a card. Ordered, and never deduplicated. */
export type CardExternalIdResponse = components["schemas"]["CardExternalIdResponse"];

/** `GET /cards/search` — one page, plus the total across every page. */
export type CardSearchResponse = components["schemas"]["CardSearchResponse"];

/** The spec §66 code an {@link ApiError} carries when the body named one. */
export type ErrorCode = components["schemas"]["ErrorCode"];

/**
 * The query parameters `GET /cards/search` accepts, taken from the generated
 * operation rather than restated, so a filter added server-side is a compile
 * error here rather than a silently ignored option.
 */
export type CardSearchParams = NonNullable<
  operations["search_cards_cards_search_get"]["parameters"]["query"]
>;

export interface ApiErrorOptions {
  /** HTTP status, when the request reached the server at all. */
  status?: number;
  /** The spec §66 code, when the body carried the error envelope. */
  code?: string;
  /** The envelope's `details`, when it carried any. */
  details?: Record<string, unknown>;
  cause?: unknown;
}

/**
 * Every failure this module raises — transport, timeout, status, and payload
 * shape alike — so callers need exactly one catch.
 *
 * `message` is written for a developer and names the URL; it is not user copy.
 * A caller that has to tell a missing card apart from an unreachable catalog
 * branches on {@link ApiError.code}, which is the contract, rather than on the
 * status, which the taxonomy lets a route override.
 */
export class ApiError extends Error {
  readonly status: number | undefined;
  readonly code: string | undefined;
  readonly details: Record<string, unknown> | undefined;

  constructor(message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
  }
}

/**
 * Re-exported so a caller needs one import to talk to the API. The variable
 * itself is owned by `./env`, which validates it — see that module.
 */
export { apiBaseUrl };

function isRecord(payload: unknown): payload is Record<string, unknown> {
  return typeof payload === "object" && payload !== null;
}

function isHealthResponse(payload: unknown): payload is HealthResponse {
  if (!isRecord(payload)) {
    return false;
  }
  return typeof payload.status === "string" && typeof payload.application_version === "string";
}

function isCardSet(payload: unknown): payload is CardSetResponse {
  if (!isRecord(payload)) {
    return false;
  }
  return (
    typeof payload.id === "string" &&
    typeof payload.set_code === "string" &&
    typeof payload.name === "string"
  );
}

function isCardSummary(payload: unknown): payload is CardSummaryResponse {
  if (!isRecord(payload)) {
    return false;
  }
  return (
    typeof payload.id === "string" &&
    typeof payload.name === "string" &&
    typeof payload.card_number === "string" &&
    typeof payload.game === "string" &&
    typeof payload.language === "string" &&
    isCardSet(payload.set)
  );
}

function isCardResponse(payload: unknown): payload is CardResponse {
  // The detail-only fields are checked before the summary narrowing, because
  // narrowing to `CardSummaryResponse` first would hide them from the compiler.
  if (!isRecord(payload) || !isRecord(payload.metadata) || !Array.isArray(payload.external_ids)) {
    return false;
  }
  return isCardSummary(payload);
}

function isCardSearchResponse(payload: unknown): payload is CardSearchResponse {
  if (!isRecord(payload)) {
    return false;
  }
  return (
    Array.isArray(payload.cards) &&
    payload.cards.every(isCardSummary) &&
    typeof payload.total === "number" &&
    typeof payload.limit === "number" &&
    typeof payload.offset === "number"
  );
}

/**
 * The caller's cancellation combined with our own deadline, so a caller that
 * unmounts still aborts and a caller that forgets a deadline still gets one.
 * `AbortSignal.timeout`/`any` are feature-detected for older environments.
 */
function requestSignal(
  caller: AbortSignal | undefined,
  timeoutMs: number,
): AbortSignal | undefined {
  const deadline =
    typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(timeoutMs) : undefined;

  if (caller === undefined) return deadline;
  if (deadline === undefined) return caller;

  return typeof AbortSignal.any === "function" ? AbortSignal.any([caller, deadline]) : caller;
}

/**
 * Lift the `{code, message, details}` envelope that
 * `services/api/src/tcg_api/errors.py` writes off a failed response.
 *
 * Everything here is best-effort on purpose. Only an `ApiError` response
 * carries that envelope: FastAPI's own request-validation 422 is a different
 * shape (`{detail: [...]}`), and a proxy in front of the service may return no
 * JSON at all. Neither may turn a clean status failure into a parse crash, so
 * an unreadable or unrecognised body simply yields no code.
 */
async function readErrorEnvelope(
  response: Response,
): Promise<Pick<ApiErrorOptions, "code" | "details">> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return {};
  }

  if (!isRecord(payload) || typeof payload.code !== "string") {
    return {};
  }

  return {
    code: payload.code,
    ...(isRecord(payload.details) ? { details: payload.details } : {}),
  };
}

interface JsonRequest<T> {
  /** Absolute URL, already carrying its query string. */
  readonly url: string;
  readonly signal: AbortSignal | undefined;
  readonly timeoutMs: number;
  readonly isPayload: (payload: unknown) => payload is T;
  /** Names the payload in the "unrecognised … payload" message. */
  readonly payloadName: string;
}

/**
 * One GET, with the four failures every endpoint here shares: the request never
 * left, the server said no, the body was not JSON, and the body was JSON the
 * contract does not describe. Each becomes an {@link ApiError}, so no caller
 * ever resolves with a partial or unrecognised payload.
 */
async function requestJson<T>(request: JsonRequest<T>): Promise<T> {
  const { url, isPayload, payloadName } = request;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: requestSignal(request.signal, request.timeoutMs),
    });
  } catch (cause) {
    throw new ApiError(`The API at ${url} could not be reached.`, { cause });
  }

  if (!response.ok) {
    throw new ApiError(`The API at ${url} returned ${response.status}.`, {
      status: response.status,
      ...(await readErrorEnvelope(response)),
    });
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch (cause) {
    throw new ApiError(`The API at ${url} returned a malformed response.`, {
      status: response.status,
      cause,
    });
  }

  if (!isPayload(payload)) {
    throw new ApiError(`The API at ${url} returned an unrecognised ${payloadName} payload.`, {
      status: response.status,
    });
  }

  return payload;
}

/**
 * Serialize search filters, dropping every absent or blank one.
 *
 * Dropping is not tidiness. `GET /cards/search` validates each filter with the
 * domain's own grammar, under which `?text=` — present but empty — is a 422, as
 * is a padded value. An unfilled form field must therefore leave the query
 * string entirely rather than travel as a blank.
 */
function searchQueryString(params: CardSearchParams): string {
  const query = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;

    if (typeof value === "number") {
      query.set(key, String(value));
      continue;
    }

    const trimmed = value.trim();
    if (trimmed !== "") {
      query.set(key, trimmed);
    }
  }

  return query.toString();
}

/**
 * Fetch the API's health. Never resolves with a partial or unrecognised
 * payload — an unexpected shape is an {@link ApiError}, not a silent default.
 */
export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/health`,
    signal,
    timeoutMs: HEALTH_TIMEOUT_MS,
    isPayload: isHealthResponse,
    payloadName: "health",
  });
}

/**
 * `GET /cards/search`.
 *
 * Filters are ANDed, and empty `params` browses the whole catalog a page at a
 * time. Nothing matching is an empty page, never a 404 — a caller must not read
 * `cards: []` as a failure.
 */
export async function searchCards(
  params: CardSearchParams = {},
  signal?: AbortSignal,
): Promise<CardSearchResponse> {
  const query = searchQueryString(params);
  const url = `${apiBaseUrl()}/cards/search${query === "" ? "" : `?${query}`}`;

  return requestJson({
    url,
    signal,
    timeoutMs: CATALOG_TIMEOUT_MS,
    isPayload: isCardSearchResponse,
    payloadName: "card search",
  });
}

/**
 * `GET /cards/{card_id}`.
 *
 * An identifier naming no card raises an {@link ApiError} whose `code` is
 * `card_not_identified`; an unreachable catalog raises `provider_error`. Branch
 * on the code rather than the status: the taxonomy lets a route override the
 * status, and this one does.
 */
export async function getCard(cardId: string, signal?: AbortSignal): Promise<CardResponse> {
  const url = `${apiBaseUrl()}/cards/${encodeURIComponent(cardId)}`;

  return requestJson({
    url,
    signal,
    timeoutMs: CATALOG_TIMEOUT_MS,
    isPayload: isCardResponse,
    payloadName: "card",
  });
}
