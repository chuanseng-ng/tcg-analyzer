import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, readResults } from "@/lib/api";

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";

/** The results before anything has been asked: `[]`, `null` and an empty map. */
const RESULTS = {
  analysis_id: ANALYSIS_ID,
  status: "analyzing",
  card_id: null,
  currency: "SGD",
  economic_configuration: null,
  market_snapshot: null,
  condition: null,
  companies: [],
  refused: {},
  recommendation: null,
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

describe("readResults", () => {
  it("reads the analysis's results with the session cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(RESULTS));
    vi.stubGlobal("fetch", fetchMock);

    await expect(readResults(ANALYSIS_ID)).resolves.toEqual(RESULTS);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/analyses/${ANALYSIS_ID}/results`);
    expect(init.credentials).toBe("include");
  });

  it("accepts the nothing-asked-yet shape as a result, not a failure", async () => {
    // `recommendation: null` and `companies: []` are a 200 (#65), and the guard
    // must not narrow on the nullable fields.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(RESULTS)));

    const results = await readResults(ANALYSIS_ID);

    expect(results.recommendation).toBeNull();
    expect(results.companies).toEqual([]);
  });

  it("passes the bare 404 through as an ApiError with no code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));

    const error = await readResults(ANALYSIS_ID).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).code).toBeUndefined();
  });

  it("rejects a body the contract does not describe", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ analysis_id: ANALYSIS_ID, companies: "none" })),
    );

    await expect(readResults(ANALYSIS_ID)).rejects.toBeInstanceOf(ApiError);
  });
});
