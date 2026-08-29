import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiBaseUrl,
  imageBytesUrl,
  listImagesAwaitingAnnotation,
  readTrainingImage,
} from "@/lib/api";

function respondWith(body: unknown, init: ResponseInit = {}) {
  return vi.fn().mockResolvedValue(
    new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
      ...init,
    }),
  );
}

const WORK_LIST = { images: [], total: 0, limit: 25, offset: 0 };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("imageBytesUrl", () => {
  it("points an <img> at the API rather than at a store", () => {
    const url = imageBytesUrl("11111111-1111-4111-8111-111111111111", "normalized");

    expect(url).toBe(
      `${apiBaseUrl()}/internal/annotation/images/` +
        "11111111-1111-4111-8111-111111111111/bytes?representation=normalized",
    );
  });

  it("encodes the identifier rather than interpolating it raw", () => {
    expect(imageBytesUrl("../secrets", "original")).toContain("%2F");
  });
});

describe("listImagesAwaitingAnnotation", () => {
  it("asks for the page it was given", async () => {
    const fetchMock = respondWith(WORK_LIST);
    vi.stubGlobal("fetch", fetchMock);

    await listImagesAwaitingAnnotation({ limit: 25, offset: 50 });

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("limit=25&offset=50");
  });

  it("sends no credentials, because this tool has no session", async () => {
    // V1 has no accounts and nothing here is scoped to one. A cookie sent by
    // habit would be a cookie somebody later reasons about.
    const fetchMock = respondWith(WORK_LIST);
    vi.stubGlobal("fetch", fetchMock);

    await listImagesAwaitingAnnotation({ limit: 25, offset: 0 });

    expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty("credentials");
  });
});

describe("every failure is an ApiError", () => {
  it("when the service cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));

    await expect(readTrainingImage("x")).rejects.toBeInstanceOf(ApiError);
  });

  it("carrying the spec §66 code when the body has one", async () => {
    vi.stubGlobal(
      "fetch",
      respondWith(
        { code: "provider_error", message: "no", details: { reason: "dataset_store_unreachable" } },
        { status: 503 },
      ),
    );

    await expect(readTrainingImage("x")).rejects.toMatchObject({
      code: "provider_error",
      status: 503,
      details: { reason: "dataset_store_unreachable" },
    });
  });

  it("keeping the status when the body is FastAPI's own 404 rather than an envelope", async () => {
    vi.stubGlobal("fetch", respondWith({ detail: "No such training image." }, { status: 404 }));

    await expect(readTrainingImage("x")).rejects.toMatchObject({ status: 404, code: undefined });
  });

  it("when the body is not JSON at all", async () => {
    vi.stubGlobal("fetch", respondWith("<html>proxy</html>"));

    await expect(readTrainingImage("x")).rejects.toBeInstanceOf(ApiError);
  });

  it("when the body is JSON but not the contract's shape", async () => {
    // The generated types are compile-time only. This is what stops a service
    // change reaching the screen as `undefined` rendered into the DOM.
    vi.stubGlobal("fetch", respondWith({ images: [{ id: 1 }], total: "many" }));

    await expect(listImagesAwaitingAnnotation({ limit: 25, offset: 0 })).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
