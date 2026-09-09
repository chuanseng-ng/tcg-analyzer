import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, mintFeedback, readFeedback, submitFeedback } from "@/lib/api";

/**
 * Spec §68's three calls (#270, #274). What is asserted here is what the code
 * is: the mint is session-scoped, the two code-addressed routes are not — weeks
 * later there is no session — and the code is carried in the path, encoded.
 */

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";
const CODE = "A3KDM-9F2QT-BXWR7-N0HJ5";

const RETURN_CODE = { return_code: CODE, expires_at: "2027-03-07T09:15:00Z" };

const SNAPSHOT = {
  card_id: "44444444-4444-4444-4444-444444444444",
  predictions: { version: "grading-psa-heuristic-v0.1.0", predictions: {} },
  recommended_action: "insufficient_information",
  model_bundle_version: "condition-compose-v0.1.0",
  grading_rules_version: "psa-rules-2026-08-24",
  created_at: "2026-09-08T09:15:00Z",
  expires_at: "2027-03-07T09:15:00Z",
};

const ANSWER = {
  status: "submitted",
  submitted_at: "2026-11-02T10:00:00Z",
  grading_company: "psa",
  grade: "9",
  designation: null,
  certification_number: "12345678",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("mintFeedback", () => {
  it("POSTs with the session cookie and no body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(RETURN_CODE, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(mintFeedback(ANALYSIS_ID)).resolves.toEqual(RETURN_CODE);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/analyses/${ANALYSIS_ID}/feedback`);
    expect(init.method).toBe("POST");
    // Session-scoped like every other analysis write: this one is asked by
    // somebody looking at their own results.
    expect(init.credentials).toBe("include");
    expect(init.body).toBeUndefined();
  });

  it("rejects a body that carries no code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ expires_at: "2027-03-07Z" })));
    await expect(mintFeedback(ANALYSIS_ID)).rejects.toBeInstanceOf(ApiError);
  });

  it("carries a second mint's 409 to the caller", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "already" }, 409)));
    await expect(mintFeedback(ANALYSIS_ID)).rejects.toMatchObject({ status: 409 });
  });
});

describe("readFeedback", () => {
  it("reads the snapshot without a session, addressing the row by the code", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SNAPSHOT));
    vi.stubGlobal("fetch", fetchMock);

    await expect(readFeedback(CODE)).resolves.toEqual(SNAPSHOT);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/feedback/${CODE}`);
    // Weeks later the cookie has expired and §53 forbids the account that
    // would carry the identity. The code is the whole of the authorisation.
    expect(init.credentials).toBeUndefined();
  });

  it("encodes a code a user mistyped rather than building a second path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SNAPSHOT));
    vi.stubGlobal("fetch", fetchMock);

    await readFeedback("../analyses");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(new URL(url).pathname).toBe("/feedback/..%2Fanalyses");
  });

  it("carries the one 404 to the caller", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "no" }, 404)));
    await expect(readFeedback(CODE)).rejects.toMatchObject({ status: 404 });
  });
});

describe("submitFeedback", () => {
  it("POSTs the answer under the code, without a session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(ANSWER));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      submitFeedback(CODE, { grading_company: "psa", grade: "9", certification_number: "12345678" }),
    ).resolves.toEqual(ANSWER);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/feedback/${CODE}`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBeUndefined();
    expect(JSON.parse(String(init.body))).toEqual({
      grading_company: "psa",
      grade: "9",
      certification_number: "12345678",
    });
  });

  it("rejects a body the contract does not describe", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ status: "submitted" })));
    await expect(
      submitFeedback(CODE, { grading_company: "psa", grade: "9" }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
