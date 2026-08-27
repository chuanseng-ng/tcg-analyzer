import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, configureEconomics, getGradingCompanies } from "@/lib/api";

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";

const COMPANIES = {
  companies: [
    {
      company: "psa",
      display_name: "PSA",
      grades: ["1", "1.5", "10"],
      rules: null,
    },
  ],
};

const CONFIGURATION = {
  id: "55555555-5555-5555-5555-555555555555",
  created_at: "2026-08-27T00:00:00Z",
  currency: "SGD",
  acquisition_cost: null,
  costs: {
    grading_fee: "40.00",
    outbound_shipping: "30.00",
    return_shipping: "30.00",
    insurance: "0.00",
    miscellaneous: "0.00",
    selling_fee: { rate: "0.1000", flat: "0.00" },
  },
  grading_companies: ["psa"],
  optimization_mode: "expected_profit",
  thresholds: {
    minimum_image_quality: 0.5,
    minimum_grade_confidence: 0.5,
    minimum_figure_confidence: 0.4,
    maximum_unpriced_probability: 0.25,
    minimum_incremental_profit: "5.00",
  },
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

describe("getGradingCompanies", () => {
  it("reads the list without the session cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(COMPANIES));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getGradingCompanies()).resolves.toEqual(COMPANIES);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe("/grading-companies");
    // The list belongs to nobody, so it does not ask for the session cookie —
    // the same reasoning the catalog reads record.
    expect(init.credentials).toBeUndefined();
  });

  it("rejects a body the contract does not describe", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ companies: [{ company: 7 }] })),
    );

    await expect(getGradingCompanies()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("configureEconomics", () => {
  it("POSTs the configuration with the session cookie, amounts as strings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(CONFIGURATION, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      configureEconomics(ANALYSIS_ID, {
        acquisition_cost: "120.00",
        grading_companies: ["psa"],
        optimization_mode: "expected_profit",
      }),
    ).resolves.toEqual(CONFIGURATION);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/analyses/${ANALYSIS_ID}/economic-configuration`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    // The service refuses a JSON number where an amount is meant: a binary
    // float cannot hold money exactly.
    expect(JSON.parse(String(init.body))).toEqual({
      acquisition_cost: "120.00",
      grading_companies: ["psa"],
      optimization_mode: "expected_profit",
    });
  });

  it("sends no costs at all when the caller has nothing to say about them", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(CONFIGURATION, 201));
    vi.stubGlobal("fetch", fetchMock);

    await configureEconomics(ANALYSIS_ID, {
      acquisition_cost: null,
      grading_companies: ["psa"],
      optimization_mode: "roi",
    });

    // Omission is how the engine's own defaults are asked for. A client that
    // filled them in would be carrying a second copy of them.
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).not.toHaveProperty("costs");
  });

  it("surfaces a second configuration as a 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "already configured" }, 409)),
    );

    const error = await configureEconomics(ANALYSIS_ID, {
      grading_companies: ["psa"],
      optimization_mode: "roi",
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    // FastAPI's own body, not the spec §66 envelope: the taxonomy has no code
    // for this and #65 declined to invent a ninth.
    expect((error as ApiError).code).toBeUndefined();
  });
});
