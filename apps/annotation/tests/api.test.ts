import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiBaseUrl,
  imageBytesUrl,
  listImagesAwaitingAnnotation,
  readTrainingImage,
  saveAnnotations,
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

/*
 * The guards, against payloads shaped like the service's real ones.
 *
 * The component tests mock `readTrainingImage`, so nothing there ever runs a
 * guard — which is how a guard that still checked a field the service had
 * stopped sending reached a browser and turned every image into "something went
 * wrong". A round trip through `requestJson` is the only thing that catches it.
 */
const SUMMARY = {
  id: "11111111-1111-4111-8111-111111111111",
  side: "front",
  card_id: null,
  physical_copy_id: "22222222-2222-4222-8222-222222222222",
  source: "first_party",
  created_at: "2026-08-01T10:00:00Z",
  has_artifact: true,
};

/** A detail is a summary plus these. Written out so a guard can be tested against it. */
const DETAIL = {
  ...SUMMARY,
  width: 1200,
  height: 1600,
  siblings: [],
  annotations: [],
  centering: [],
};

const STORED_MARKER = {
  id: "33333333-3333-4333-8333-333333333333",
  kind: "corner",
  region: "top_left",
  label: "whitening",
  severity: "minor",
  confidence: 0.8,
  bbox: { x: 0.01, y: 0.02, width: 0.06, height: 0.05 },
  annotator_id: "annotator",
  created_at: "2026-08-29T10:00:00Z",
};

describe("the payload guards accept what the service actually sends", () => {
  it("accepts a work list", async () => {
    vi.stubGlobal("fetch", respondWith({ images: [SUMMARY], total: 1, limit: 25, offset: 0 }));

    const page = await listImagesAwaitingAnnotation({ limit: 25, offset: 0 });

    expect(page.images[0]?.id).toBe(SUMMARY.id);
  });

  it("accepts an image detail with a sibling", async () => {
    vi.stubGlobal("fetch", respondWith({ ...DETAIL, siblings: [{ ...SUMMARY, side: "back" }] }));

    const image = await readTrainingImage(SUMMARY.id);

    expect(image.has_artifact).toBe(true);
    expect(image.siblings).toHaveLength(1);
  });

  it("accepts an image detail with no siblings and no artifact", async () => {
    vi.stubGlobal("fetch", respondWith({ ...DETAIL, has_artifact: false }));

    await expect(readTrainingImage(SUMMARY.id)).resolves.toMatchObject({ has_artifact: false });
  });

  it("accepts an image detail carrying annotations already recorded", async () => {
    vi.stubGlobal(
      "fetch",
      respondWith({
        ...DETAIL,
        annotations: [STORED_MARKER],
        centering: [
          {
            id: "44444444-4444-4444-8444-444444444444",
            horizontal: 0.52,
            vertical: null,
            confidence: 0.9,
            notes: null,
            annotator_id: "annotator",
            created_at: "2026-08-29T10:00:00Z",
          },
        ],
      }),
    );

    const image = await readTrainingImage(SUMMARY.id);

    expect(image.annotations).toHaveLength(1);
    expect(image.centering[0]?.vertical).toBeNull();
  });

  it("still refuses a detail missing a field it depends on", async () => {
    // Guard the guard: an `isImage` that returned true unconditionally would
    // pass every assertion above.
    vi.stubGlobal("fetch", respondWith({ ...DETAIL, height: undefined }));

    await expect(readTrainingImage(SUMMARY.id)).rejects.toBeInstanceOf(ApiError);
  });

  it("refuses a detail whose annotations the service stopped sending", async () => {
    // The regression #159 shipped, in the other direction: a field added to the
    // response and not to the guard is the same bug as one removed from the
    // response and left in the guard.
    vi.stubGlobal("fetch", respondWith({ ...DETAIL, annotations: undefined }));

    await expect(readTrainingImage(SUMMARY.id)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("saving an annotation", () => {
  it("posts to the image's own annotations path, as JSON and without credentials", async () => {
    const fetchMock = respondWith({ markers: [STORED_MARKER], centering: [] });
    vi.stubGlobal("fetch", fetchMock);

    const body = {
      markers: [
        {
          kind: "corner" as const,
          region: "top_left" as const,
          label: "whitening" as const,
          severity: "minor" as const,
          confidence: 0.8,
          bbox: { x: 0.01, y: 0.02, width: 0.06, height: 0.05 },
        },
      ],
      centering: null,
    };

    const stored = await saveAnnotations(SUMMARY.id, body);

    expect(stored.markers).toHaveLength(1);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`http://localhost:8000/internal/annotation/images/${SUMMARY.id}/annotations`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(body);
    // A write is exactly where somebody reaches for `credentials`. This surface
    // has no session, and the key's absence is the assertion.
    expect(init).not.toHaveProperty("credentials");
  });

  it("turns a refusal into an ApiError carrying its status", async () => {
    vi.stubGlobal("fetch", respondWith({ detail: "no artifact" }, { status: 409 }));

    await expect(
      saveAnnotations(SUMMARY.id, { markers: [], centering: null }),
    ).rejects.toMatchObject({ status: 409 });
  });
});
