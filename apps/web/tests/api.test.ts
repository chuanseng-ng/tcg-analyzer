import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiBaseUrl, getHealth } from "@/lib/api";
import { apiBaseUrl as validatedApiBaseUrl } from "@/lib/env";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("apiBaseUrl", () => {
  // The variable's own parsing rules are tested in `env.test.ts`, which owns
  // it. What matters here is that the re-export is the same function, so a
  // caller importing it from `@/lib/api` gets the validated one.
  it("is the validated accessor from lib/env", () => {
    expect(apiBaseUrl).toBe(validatedApiBaseUrl);
  });
});

describe("getHealth", () => {
  it("parses the health payload", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ status: "ok", application_version: "0.0.0" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getHealth()).resolves.toEqual({
      status: "ok",
      application_version: "0.0.0",
    });
  });

  it("requests /health on the configured base URL without caching it", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ status: "ok", application_version: "0.0.0" }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");

    await getHealth();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/health");
    expect(init.cache).toBe("no-store");
    expect(init.signal).toBeDefined();
  });

  it("throws ApiError carrying the status on a 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "boom" }, 500)));

    await expect(getHealth()).rejects.toBeInstanceOf(ApiError);
    await expect(getHealth()).rejects.toMatchObject({ status: 500 });
  });

  it("throws ApiError when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(getHealth()).rejects.toBeInstanceOf(ApiError);
    await expect(getHealth()).rejects.toMatchObject({ status: undefined });
  });

  it("throws ApiError when the response is not the health contract", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ unexpected: true })));

    await expect(getHealth()).rejects.toBeInstanceOf(ApiError);
  });
});
