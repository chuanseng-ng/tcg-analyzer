/**
 * Client for the FastAPI service in `services/api`.
 *
 * NOTE: the response types below are hand-written against the frozen `/health`
 * contract. They are temporary. Per
 * `docs/adr/0001-language-boundaries-in-the-monorepo.md` the frontend/backend
 * contract is the OpenAPI schema, and #21 replaces this file's types with ones
 * generated from it. Do not grow a hand-maintained type surface here in the
 * meantime — add the endpoint to the schema instead.
 */

const DEFAULT_API_BASE_URL = "http://localhost:8000";

/** Health checks are a liveness signal; a slow answer is a failed one. */
const HEALTH_TIMEOUT_MS = 5_000;

/** `GET /health` — see services/api. */
export interface HealthResponse {
  status: string;
  application_version: string;
}

export interface ApiErrorOptions {
  /** HTTP status, when the request reached the server at all. */
  status?: number;
  cause?: unknown;
}

/**
 * Every failure this module raises — transport, timeout, status, and payload
 * shape alike — so callers need exactly one catch.
 */
export class ApiError extends Error {
  readonly status: number | undefined;

  constructor(message: string, options: ApiErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.status = options.status;
  }
}

/**
 * Base URL of the API, without a trailing slash.
 *
 * Read as a literal `process.env.NEXT_PUBLIC_*` member so Next can inline it
 * into the browser bundle at build time.
 */
export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  return configured ? configured.replace(/\/+$/, "") : DEFAULT_API_BASE_URL;
}

function isHealthResponse(payload: unknown): payload is HealthResponse {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const candidate = payload as Record<string, unknown>;
  return typeof candidate.status === "string" && typeof candidate.application_version === "string";
}

/**
 * The caller's cancellation combined with our own deadline, so a caller that
 * unmounts still aborts and a caller that forgets a deadline still gets one.
 * `AbortSignal.timeout`/`any` are feature-detected for older environments.
 */
function requestSignal(caller: AbortSignal | undefined): AbortSignal | undefined {
  const deadline =
    typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(HEALTH_TIMEOUT_MS) : undefined;

  if (caller === undefined) return deadline;
  if (deadline === undefined) return caller;

  return typeof AbortSignal.any === "function" ? AbortSignal.any([caller, deadline]) : caller;
}

/**
 * Fetch the API's health. Never resolves with a partial or unrecognised
 * payload — an unexpected shape is an {@link ApiError}, not a silent default.
 */
export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const url = `${apiBaseUrl()}/health`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: requestSignal(signal),
    });
  } catch (cause) {
    throw new ApiError(`The API at ${url} could not be reached.`, { cause });
  }

  if (!response.ok) {
    throw new ApiError(`The API at ${url} returned ${response.status}.`, {
      status: response.status,
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

  if (!isHealthResponse(payload)) {
    throw new ApiError(`The API at ${url} returned an unrecognised health payload.`, {
      status: response.status,
    });
  }

  return payload;
}
