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
 * Starting an analysis is one INSERT and a cookie, so it gets the catalog's
 * deadline rather than health's. The image upload that follows has none at all
 * — see {@link uploadImage}.
 */
const ANALYSIS_TIMEOUT_MS = 10_000;

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

/** `POST /analyses` and `GET /analyses/{id}` — one analysis, as the API reports it. */
export type AnalysisResponse = components["schemas"]["AnalysisResponse"];

/** `POST /analyses/{id}/images` — one stored photograph. */
export type ImageResponse = components["schemas"]["ImageResponse"];

/** `POST /analyses/{id}/run`'s acknowledgement. `queued` is not a state (§65). */
export type AnalysisRunResponse = components["schemas"]["AnalysisRunResponse"];

/** `GET /grading-companies` — the companies, their scales and their published rules. */
export type GradingCompaniesResponse = components["schemas"]["GradingCompaniesResponse"];

/**
 * One company, as a client needs to render it. **The scale is the whole reason
 * this endpoint exists**: PSA and TAG issue no 9.5 and BGS does, so a screen
 * that spelled a shared scale of its own would misrender one of them.
 */
export type GradingCompanyResponse = components["schemas"]["GradingCompanyResponse"];

/**
 * `POST /analyses/{id}/economic-configuration` — what the user says the
 * economics of their decision are (spec §45, §46, §43).
 *
 * Every cost field is optional and **the endpoint defaults it from the engine's
 * own placeholders**. That is why nothing in `apps/web` writes a cost down: a
 * second copy of `40.00` here would drift from the one the recommendation is
 * actually computed against, silently.
 */
export type EconomicConfigurationRequest = components["schemas"]["EconomicConfigurationRequest"];

/** The stored configuration, read back exactly as it was written. */
export type EconomicConfigurationResponse = components["schemas"]["EconomicConfigurationResponse"];

/**
 * `GET /analyses/{id}/results` — what the analysis has arrived at (spec §41, §44, §49).
 *
 * `companies` is `[]` and `recommendation` is `null` until a configuration and a
 * stored prediction exist, and that `null` is deliberately not
 * `insufficient_information` (#65): the first means nobody has asked, the second
 * that the engine was asked and declined. `refused` and `companies` together are
 * every configured company (#238).
 */
export type ResultsResponse = components["schemas"]["ResultsResponse"];

/** Spec §44's answer, once something has been asked. */
export type RecommendationResponse = components["schemas"]["RecommendationResponse"];

/** `code`/`figure`/`value`/`threshold` and no sentence (#64) — the copy is this app's. */
export type ReasonResponse = components["schemas"]["ReasonResponse"];

/** Every M5 figure for one company, each present-and-null beside its own reason. */
export type CompanyEconomicsResponse = components["schemas"]["CompanyEconomicsResponse"];

/** One term of a grade distribution — spec §2.1's `P(g)`, keyed as `GET /grading-companies` spells the grade. */
export type GradeProbabilityResponse = components["schemas"]["GradeProbabilityResponse"];

/** The snapshot the figures were priced against — what ADR 0006 requires the UI to date-stamp. */
export type MarketSnapshotReference = components["schemas"]["MarketSnapshotReference"];

/**
 * Which view of the card an upload is.
 *
 * Taken from the operation rather than restated, so a side the server starts or
 * stops accepting — spec §52's guided-photography views, say — is a compile
 * error here rather than a 422 a user discovers.
 */
export type UploadSide =
  operations["upload_image_analyses__analysis_id__images_post"]["parameters"]["query"]["side"];

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
  /**
   * The envelope's `message` — copy the service wrote for a reader, not for a
   * developer. Only `invalid_image` currently has a message worth showing.
   */
  serverMessage?: string;
  /**
   * `Retry-After`, in seconds, when the response carried one. A 429 is
   * `HTTPException` outside the spec §66 envelope (ADR 0005), so this header is
   * the only thing that distinguishes it from any other refusal.
   */
  retryAfterSeconds?: number;
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
  readonly serverMessage: string | undefined;
  readonly retryAfterSeconds: number | undefined;

  constructor(message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.serverMessage = options.serverMessage;
    this.retryAfterSeconds = options.retryAfterSeconds;
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

function isAnalysisResponse(payload: unknown): payload is AnalysisResponse {
  if (!isRecord(payload)) {
    return false;
  }
  // `card_id` and `completed_at` are nullable rather than optional, so their
  // presence is not what narrows this — `id` and `status` are.
  return typeof payload.id === "string" && typeof payload.status === "string";
}

function isAnalysisRunResponse(payload: unknown): payload is AnalysisRunResponse {
  if (!isRecord(payload)) {
    return false;
  }
  return typeof payload.analysis_id === "string" && payload.status === "queued";
}

function isGradingCompaniesResponse(payload: unknown): payload is GradingCompaniesResponse {
  if (!isRecord(payload) || !Array.isArray(payload.companies)) {
    return false;
  }
  return payload.companies.every(
    (company) =>
      isRecord(company) &&
      typeof company.company === "string" &&
      typeof company.display_name === "string" &&
      Array.isArray(company.grades),
  );
}

function isEconomicConfigurationResponse(
  payload: unknown,
): payload is EconomicConfigurationResponse {
  if (!isRecord(payload) || !isRecord(payload.costs) || !isRecord(payload.thresholds)) {
    return false;
  }
  // `acquisition_cost` is nullable rather than optional, so its presence is not
  // what narrows this — and a payload that omitted it would be one where the
  // screen could not tell "not supplied" from "not sent".
  return (
    typeof payload.id === "string" &&
    typeof payload.optimization_mode === "string" &&
    typeof payload.currency === "string" &&
    Array.isArray(payload.grading_companies)
  );
}

function isResultsResponse(payload: unknown): payload is ResultsResponse {
  if (!isRecord(payload) || !isRecord(payload.refused)) {
    return false;
  }
  // `recommendation`, `condition`, `market_snapshot` and `economic_configuration`
  // are nullable rather than optional, so none of them narrows this: `null` in
  // each is the "nothing asked yet" result #65 makes a 200, not a bad payload.
  return (
    typeof payload.analysis_id === "string" &&
    typeof payload.status === "string" &&
    typeof payload.currency === "string" &&
    Array.isArray(payload.companies)
  );
}

function isImageResponse(payload: unknown): payload is ImageResponse {
  if (!isRecord(payload)) {
    return false;
  }
  return (
    typeof payload.id === "string" &&
    typeof payload.analysis_id === "string" &&
    typeof payload.side === "string" &&
    typeof payload.sha256 === "string" &&
    typeof payload.analysis_status === "string"
  );
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

  return liftEnvelope(payload);
}

/**
 * The envelope's fields, off an already-parsed body. Shared by the `fetch` path
 * above and the `XMLHttpRequest` path below, which read the same envelope from
 * two different transports and must not disagree about it.
 */
function liftEnvelope(
  payload: unknown,
): Pick<ApiErrorOptions, "code" | "serverMessage" | "details"> {
  if (!isRecord(payload) || typeof payload.code !== "string") {
    return {};
  }

  return {
    code: payload.code,
    ...(typeof payload.message === "string" ? { serverMessage: payload.message } : {}),
    ...(isRecord(payload.details) ? { details: payload.details } : {}),
  };
}

/**
 * `Retry-After` in seconds, when the response carried a usable one.
 *
 * Only the delta-seconds form is read. The HTTP-date form is legal and this
 * service never sends it (`tcg_api.rate_limit` writes an integer), and a
 * caller that guessed wrong would count down from a nonsense number.
 */
export function readRetryAfter(header: string | null): number | undefined {
  if (header === null) return undefined;
  const seconds = Number.parseInt(header.trim(), 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined;
}

/**
 * Everything a failed response can tell a caller: its status, the spec §66
 * envelope if it carried one, and `Retry-After` if it carried that.
 *
 * One function so the two transports below — `fetch` for JSON and
 * `XMLHttpRequest` for the upload, which needs progress events `fetch` cannot
 * give — cannot come to different conclusions about the same refusal.
 */
async function readFailure(response: Response): Promise<ApiErrorOptions> {
  const retryAfterSeconds = readRetryAfter(response.headers.get("retry-after"));
  return {
    status: response.status,
    ...(retryAfterSeconds === undefined ? {} : { retryAfterSeconds }),
    ...(await readErrorEnvelope(response)),
  };
}

interface JsonRequest<T> {
  /** Absolute URL, already carrying its query string. */
  readonly url: string;
  /** Defaults to GET; every catalog read is one. */
  readonly method?: "GET" | "POST";
  /**
   * `"include"` for anything scoped to the anonymous session, so the browser
   * sends and stores the HTTP-only `tcg_session` cookie `POST /analyses` issues
   * (#32). The catalog reads belong to nobody and deliberately do not ask for it.
   */
  readonly credentials?: RequestCredentials;
  /**
   * Sent as JSON when present. The analysis writes are the only requests here
   * that carry one; a catalog read has nothing to say.
   */
  readonly body?: unknown;
  readonly signal: AbortSignal | undefined;
  readonly timeoutMs: number;
  readonly isPayload: (payload: unknown) => payload is T;
  /** Names the payload in the "unrecognised … payload" message. */
  readonly payloadName: string;
}

/**
 * One JSON request, with the four failures every endpoint here shares: the
 * request never left, the server said no, the body was not JSON, and the body
 * was JSON the contract does not describe. Each becomes an {@link ApiError}, so
 * no caller ever resolves with a partial or unrecognised payload.
 */
async function requestJson<T>(request: JsonRequest<T>): Promise<T> {
  const { url, isPayload, payloadName } = request;

  let response: Response;
  try {
    response = await fetch(url, {
      method: request.method ?? "GET",
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(request.body === undefined ? {} : { "content-type": "application/json" }),
      },
      ...(request.body === undefined ? {} : { body: JSON.stringify(request.body) }),
      ...(request.credentials === undefined ? {} : { credentials: request.credentials }),
      signal: requestSignal(request.signal, request.timeoutMs),
    });
  } catch (cause) {
    throw new ApiError(`The API at ${url} could not be reached.`, { cause });
  }

  if (!response.ok) {
    throw new ApiError(
      `The API at ${url} returned ${response.status}.`,
      await readFailure(response),
    );
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

/**
 * `POST /analyses` — start an analysis, opening an anonymous session if the
 * browser has none (spec §53).
 *
 * `credentials: "include"` is the whole point: the service answers with an
 * HTTP-only `tcg_session` cookie, and without this the browser neither stores
 * it nor sends it back, so every later call about this analysis would be a 404.
 *
 * Rate-limited (ADR 0005). A 429 arrives as an {@link ApiError} carrying
 * `retryAfterSeconds`, and a caller must wait rather than retry immediately.
 */
export async function startAnalysis(signal?: AbortSignal): Promise<AnalysisResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/analyses`,
    method: "POST",
    credentials: "include",
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
    isPayload: isAnalysisResponse,
    payloadName: "analysis",
  });
}

/**
 * `POST /analyses/{id}/run` — hand the analysis to a worker (spec §8, §65).
 *
 * Answers `queued`, which is an acknowledgement rather than a state: the worker
 * moves the analysis, and `GET /analyses/{id}` is where its state is read. Only
 * an analysis whose photographs have both arrived may run; anything else is a
 * 409.
 *
 * Rate-limited, sharing one bucket with the other analysis writes (ADR 0005).
 */
export async function runAnalysis(
  analysisId: string,
  signal?: AbortSignal,
): Promise<AnalysisRunResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/analyses/${encodeURIComponent(analysisId)}/run`,
    method: "POST",
    credentials: "include",
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
    isPayload: isAnalysisRunResponse,
    payloadName: "run acknowledgement",
  });
}

/**
 * `GET /analyses/{id}` — the state of one analysis, and what the quality gate
 * made of its photographs (spec §65, §19).
 *
 * The polling endpoint. Deliberately not rate-limited, because §65 requires a
 * client to poll it and throttling that would throttle the product's own
 * progress reporting (ADR 0005). One 404 covers an unknown analysis, someone
 * else's, a missing cookie and a lapsed one.
 */
export async function readAnalysis(
  analysisId: string,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/analyses/${encodeURIComponent(analysisId)}`,
    credentials: "include",
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
    isPayload: isAnalysisResponse,
    payloadName: "analysis",
  });
}

/**
 * `GET /analyses/{id}/results` — the economics and the recommendation, composed
 * from what the analysis recorded (spec §41, §44, §49).
 *
 * Answers 200 in every state, with `[]` and `null` where nothing has been asked
 * yet; a client that wants a finished answer waits for `completed` on
 * {@link readAnalysis} first (#244) and reads once. Not rate-limited, and the
 * same bare 404 as every other analysis route. `no-store` server-side, because
 * every confidence in the body is discounted for the prices' age at the moment
 * of asking.
 */
export async function readResults(
  analysisId: string,
  signal?: AbortSignal,
): Promise<ResultsResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/analyses/${encodeURIComponent(analysisId)}/results`,
    credentials: "include",
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
    isPayload: isResultsResponse,
    payloadName: "results",
  });
}

/**
 * `POST /analyses/{id}/confirm-card` — record which card this is (spec §20).
 *
 * The identifier is resolved against the catalog server-side, so one naming no
 * card is a 404 carrying `card_not_identified` — the same code
 * `GET /cards/{id}` answers with. An analysis that is not waiting for a
 * confirmation is a 409, which includes one that has already been confirmed:
 * §65 moves forwards only, so there is no second confirmation.
 */
export async function confirmCard(
  analysisId: string,
  cardId: string,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/analyses/${encodeURIComponent(analysisId)}/confirm-card`,
    method: "POST",
    credentials: "include",
    body: { card_id: cardId },
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
    isPayload: isAnalysisResponse,
    payloadName: "analysis",
  });
}

/**
 * `GET /grading-companies` — the supported companies, their grade scales and the
 * version of each published standard in force today (spec §64, §22, §23).
 *
 * Deliberately **not** session-scoped: the list belongs to nobody, so it is sent
 * without credentials like the catalog reads. Everything a screen needs to name
 * a company — its slug, its display name and its scale — comes from here rather
 * than from a constant, which is what lets a fourth company appear with no
 * frontend change.
 */
export async function getGradingCompanies(signal?: AbortSignal): Promise<GradingCompaniesResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/grading-companies`,
    signal,
    timeoutMs: CATALOG_TIMEOUT_MS,
    isPayload: isGradingCompaniesResponse,
    payloadName: "grading companies",
  });
}

/**
 * `POST /analyses/{id}/economic-configuration` — record the economics of the
 * decision (spec §45, §46, §43).
 *
 * **Amounts travel as decimal strings, in as well as out.** The service refuses
 * a JSON number where an amount is meant, because a JSON number is a binary
 * float in most clients and money must stay exact — so a caller builds the
 * request from strings and never from `Number`.
 *
 * **Omitting a cost is how the engine's default is asked for**, and omitting
 * `acquisition_cost` — or sending `null` — is how "I don't know what I paid" is
 * said. `"0.00"` is a real acquisition cost and reaches a different answer.
 *
 * Written once, while the analysis is `analyzing`, and recording it is what
 * completes the analysis: a second call finds `completed` and is a 409, and so
 * is one made in any other state. Rate-limited, sharing one bucket with the
 * other analysis writes (ADR 0005).
 */
export async function configureEconomics(
  analysisId: string,
  request: EconomicConfigurationRequest,
  signal?: AbortSignal,
): Promise<EconomicConfigurationResponse> {
  return requestJson({
    url: `${apiBaseUrl()}/analyses/${encodeURIComponent(analysisId)}/economic-configuration`,
    method: "POST",
    credentials: "include",
    body: request,
    signal,
    timeoutMs: ANALYSIS_TIMEOUT_MS,
    isPayload: isEconomicConfigurationResponse,
    payloadName: "economic configuration",
  });
}

export interface UploadImageRequest {
  readonly analysisId: string;
  readonly side: UploadSide;
  /** The photograph itself. Sent as the raw body — this endpoint takes no form. */
  readonly file: Blob;
  /**
   * How much of the body has left the browser, 0 to 1, or `null` when the
   * browser cannot say. Called repeatedly while the upload is in flight.
   */
  readonly onProgress?: (fraction: number | null) => void;
  readonly signal?: AbortSignal;
}

/**
 * `POST /analyses/{id}/images?side=` — one side of the card.
 *
 * **`XMLHttpRequest` rather than `fetch`, and that is not nostalgia.** `fetch`
 * has no upload-progress event at all: reporting it requires a streaming
 * request body, which needs HTTP/2 plus `duplex: "half"` and which Safari does
 * not implement — and a phone is this screen's primary device. Spec §48 lists
 * upload progress as a requirement, so the transport is chosen by the
 * requirement rather than the other way round. Everything else about the call
 * is identical, including that a failure is an {@link ApiError} carrying the
 * same `status`, `code` and `retryAfterSeconds` the `fetch` path would give.
 *
 * There is deliberately no timeout. A 15 MB photograph over a weak mobile
 * connection is legitimately slow, and any finite deadline either kills that
 * upload or is long enough to be useless; the progress bar is what tells a user
 * the transfer has stalled, and `signal` is what cancels it.
 */
export function uploadImage(request: UploadImageRequest): Promise<ImageResponse> {
  const { analysisId, side, file, onProgress, signal } = request;
  const url = `${apiBaseUrl()}/analyses/${encodeURIComponent(analysisId)}/images?side=${side}`;

  return new Promise<ImageResponse>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError(`The upload to ${url} was cancelled.`));
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    // The counterpart of `credentials: "include"`: without it the session
    // cookie is not sent and the endpoint answers 404, not 401.
    xhr.withCredentials = true;
    xhr.setRequestHeader("accept", "application/json");

    if (onProgress) {
      xhr.upload.onprogress = (event) => {
        onProgress(event.lengthComputable ? event.loaded / event.total : null);
      };
    }

    const abort = () => xhr.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const done = () => signal?.removeEventListener("abort", abort);

    xhr.onerror = () => {
      done();
      // No status: the request never reached the service, which is the same
      // fact to a caller as a `fetch` that threw.
      reject(new ApiError(`The API at ${url} could not be reached.`));
    };

    xhr.onabort = () => {
      done();
      reject(new ApiError(`The upload to ${url} was cancelled.`));
    };

    xhr.onload = () => {
      done();

      let payload: unknown;
      try {
        payload = JSON.parse(xhr.responseText) as unknown;
      } catch {
        payload = undefined;
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        const retryAfterSeconds = readRetryAfter(xhr.getResponseHeader("retry-after"));
        reject(
          new ApiError(`The API at ${url} returned ${xhr.status}.`, {
            status: xhr.status,
            ...(retryAfterSeconds === undefined ? {} : { retryAfterSeconds }),
            ...liftEnvelope(payload),
          }),
        );
        return;
      }

      if (!isImageResponse(payload)) {
        reject(
          new ApiError(`The API at ${url} returned an unrecognised image payload.`, {
            status: xhr.status,
          }),
        );
        return;
      }

      resolve(payload);
    };

    xhr.send(file);
  });
}
