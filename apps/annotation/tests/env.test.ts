import { afterEach, describe, expect, it, vi } from "vitest";

import { apiBaseUrl } from "@/lib/env";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("apiBaseUrl", () => {
  it("falls back to the local FastAPI service when unset", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", undefined);

    expect(apiBaseUrl()).toBe("http://localhost:8000");
  });

  it("falls back when the variable is present but blank", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "   ");

    expect(apiBaseUrl()).toBe("http://localhost:8000");
  });

  it("prefers the configured base URL", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");

    expect(apiBaseUrl()).toBe("https://api.example.test");
  });

  it("strips trailing slashes so paths join cleanly", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test//");

    expect(apiBaseUrl()).toBe("https://api.example.test");
  });

  it("rejects a value that is not a URL, naming the variable", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "localhost:8000");

    expect(() => apiBaseUrl()).toThrow(/NEXT_PUBLIC_API_BASE_URL/);
  });

  it("rejects a non-HTTP scheme", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "file:///etc/passwd");

    expect(() => apiBaseUrl()).toThrow(/NEXT_PUBLIC_API_BASE_URL/);
  });
});
