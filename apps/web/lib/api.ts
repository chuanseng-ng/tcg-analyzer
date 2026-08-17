/**
 * Client for the FastAPI service in `services/api`.
 *
 * Response types come from `./api-types`, generated from the service's OpenAPI
 * schema — see `docs/adr/0001-language-boundaries-in-the-monorepo.md`. The
 * schema is the frontend/backend contract, so do not hand-write a response
 * type here: add the endpoint to the schema and regenerate with
 * `pnpm --filter @tcg/web gen:api-types`. CI fails if the two drift apart.
 */

import type { components } from "./api-types";
import { apiBaseUrl } from "./env";

/** Health checks are a liveness signal; a slow answer is a failed one. */
const HEALTH_TIMEOUT_MS = 5_000;

/**
 * `GET /health`.
 *
 * Aliased rather than re-declared so that a change to the server's model is a
 * compile error here, not a silent mismatch. `status` is narrower than it looks
 * — the schema pins it to the literal `"ok"`.
 */
export type HealthResponse = components["schemas"]["HealthResponse"];

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
 * Re-exported so a caller needs one import to talk to the API. The variable
 * itself is owned by `./env`, which validates it — see that module.
 */
export { apiBaseUrl };

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
