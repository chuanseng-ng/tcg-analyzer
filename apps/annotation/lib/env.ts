/**
 * Validated environment configuration for the web app.
 *
 * The counterpart to `services/api/src/tcg_api/config.py`: one module that
 * knows every variable, so nothing else reads `process.env` directly and every
 * variable is guaranteed to reach `.env.example`.
 *
 * Same rule as the API — fail fast on malformed, tolerate absent. An unset
 * variable falls back to the local development default; a *wrong* one throws
 * with the variable's name, because a base URL that silently does not work
 * turns into an unexplained failed fetch several layers away.
 *
 * `NEXT_PUBLIC_*` values are inlined into the browser bundle at build time, so
 * none of them may ever be a secret.
 */

const API_BASE_URL_VARIABLE = "NEXT_PUBLIC_API_BASE_URL";

/** The FastAPI service as `uv run uvicorn tcg_api.main:app` serves it. */
const DEFAULT_API_BASE_URL = "http://localhost:8000";

/**
 * Base URL of the API, without a trailing slash.
 *
 * The `process.env.NEXT_PUBLIC_API_BASE_URL` member expression is written out
 * literally rather than looked up through {@link API_BASE_URL_VARIABLE}: Next
 * substitutes the value at build time by matching the literal text, and an
 * indexed read would leave `undefined` in the browser bundle.
 *
 * @throws if the variable is set to something that is not an absolute HTTP URL.
 */
export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (!configured) {
    return DEFAULT_API_BASE_URL;
  }

  let parsed: URL;
  try {
    parsed = new URL(configured);
  } catch {
    throw new Error(
      `${API_BASE_URL_VARIABLE} is not an absolute URL: ${JSON.stringify(configured)}. ` +
        `Expected something like ${DEFAULT_API_BASE_URL}.`,
    );
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(
      `${API_BASE_URL_VARIABLE} must use http or https, not ${JSON.stringify(parsed.protocol)}.`,
    );
  }

  return configured.replace(/\/+$/, "");
}
