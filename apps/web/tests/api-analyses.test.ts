import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, confirmCard, runAnalysis, startAnalysis, uploadImage } from "@/lib/api";

const ANALYSIS = {
  id: "33333333-3333-3333-3333-333333333333",
  status: "created",
  created_at: "2026-08-21T00:00:00Z",
  completed_at: null,
  card_id: null,
};

const IMAGE = {
  id: "44444444-4444-4444-4444-444444444444",
  analysis_id: ANALYSIS.id,
  side: "front",
  mime_type: "image/jpeg",
  sha256: "a".repeat(64),
  created_at: "2026-08-21T00:00:01Z",
  analysis_status: "uploading",
};

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("startAnalysis", () => {
  it("POSTs and sends the session cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(ANALYSIS, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(startAnalysis()).resolves.toEqual(ANALYSIS);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe("/analyses");
    expect(init.method).toBe("POST");
    // Without this the browser neither stores nor returns the HTTP-only
    // `tcg_session` cookie, and every later call is a 404.
    expect(init.credentials).toBe("include");
  });

  it("surfaces a 429 with its Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(jsonResponse({ detail: "slow down" }, 429, { "retry-after": "42" })),
    );

    const error = await startAnalysis().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(429);
    // A 429 is outside the spec §66 envelope (ADR 0005), so it carries no code
    // and the header is the only thing that distinguishes it.
    expect((error as ApiError).code).toBeUndefined();
    expect((error as ApiError).retryAfterSeconds).toBe(42);
  });

  it("rejects a body the contract does not describe", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ id: 7 }, 201)));

    await expect(startAnalysis()).rejects.toBeInstanceOf(ApiError);
  });
});

/**
 * A stand-in for the browser's `XMLHttpRequest`, driven by the test.
 *
 * `fetch` is not used for the upload because it cannot report upload progress
 * in Safari, and a phone is this screen's primary device — so the fake has to
 * be an XHR-shaped one.
 */
class FakeXhr {
  static last: FakeXhr | undefined;

  method = "";
  url = "";
  withCredentials = false;
  readonly headers: Record<string, string> = {};
  sent: unknown;
  aborted = false;

  status = 200;
  responseText = "";
  private responseHeaders: Record<string, string> = {};

  readonly upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    FakeXhr.last = this;
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }

  getResponseHeader(name: string): string | null {
    return this.responseHeaders[name.toLowerCase()] ?? null;
  }

  send(body: unknown) {
    this.sent = body;
  }

  abort() {
    this.aborted = true;
    this.onabort?.();
  }

  /** Report progress as the browser would while the body is going out. */
  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent);
  }

  /** Complete the request with a status, a body, and any response headers. */
  finish(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.responseHeaders = headers;
    this.onload?.();
  }
}

function stubXhr(): typeof FakeXhr {
  FakeXhr.last = undefined;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return FakeXhr;
}

function photograph(): File {
  return new File([new Uint8Array(1024)], "photo.jpg", { type: "image/jpeg" });
}

function pending() {
  const file = photograph();
  const progress = vi.fn();
  const result = uploadImage({
    analysisId: ANALYSIS.id,
    side: "front",
    file,
    onProgress: progress,
  });
  // `uploadImage` builds the request synchronously, so the fake is already here.
  const xhr = FakeXhr.last as FakeXhr;
  return { file, progress, result, xhr };
}

describe("runAnalysis", () => {
  it("POSTs with the session cookie and reads the acknowledgement", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ analysis_id: ANALYSIS.id, status: "queued" }, 202));
    vi.stubGlobal("fetch", fetchMock);

    await expect(runAnalysis(ANALYSIS.id)).resolves.toEqual({
      analysis_id: ANALYSIS.id,
      status: "queued",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/analyses/${ANALYSIS.id}/run`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
  });

  it("refuses an acknowledgement that does not say queued", async () => {
    // `queued` is the only thing this endpoint answers; anything else means the
    // contract has moved and the caller must not carry on as though it had not.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ analysis_id: ANALYSIS.id })));

    await expect(runAnalysis(ANALYSIS.id)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("confirmCard", () => {
  const CARD_ID = "22222222-2222-2222-2222-222222222222";

  it("sends the card as JSON, with the session cookie", async () => {
    const confirmed = { ...ANALYSIS, status: "analyzing", card_id: CARD_ID };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(confirmed));
    vi.stubGlobal("fetch", fetchMock);

    await expect(confirmCard(ANALYSIS.id, CARD_ID)).resolves.toEqual(confirmed);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe(`/analyses/${ANALYSIS.id}/confirm-card`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({ card_id: CARD_ID });
  });

  it("carries the spec §66 code off a card the catalog does not hold", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { code: "card_not_identified", message: "No card is recorded.", details: {} },
          404,
        ),
      ),
    );

    const error = await confirmCard(ANALYSIS.id, CARD_ID).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("card_not_identified");
  });
});

describe("uploadImage", () => {
  it("POSTs the raw file to the side's URL with the session cookie", async () => {
    stubXhr();
    const { file, result, xhr } = pending();
    xhr.finish(201, IMAGE);

    await expect(result).resolves.toEqual(IMAGE);

    expect(xhr.method).toBe("POST");
    const url = new URL(xhr.url);
    expect(url.pathname).toBe(`/analyses/${ANALYSIS.id}/images`);
    expect(url.searchParams.get("side")).toBe("front");
    // The endpoint takes no multipart form and no filename (#33, spec §55).
    expect(xhr.sent).toBe(file);
    expect(xhr.withCredentials).toBe(true);
  });

  it("reports how much of the body has left the browser", async () => {
    stubXhr();
    const { progress, result, xhr } = pending();

    xhr.progress(256, 1024);
    xhr.progress(1024, 1024);
    xhr.finish(201, IMAGE);
    await result;

    expect(progress.mock.calls).toEqual([[0.25], [1]]);
  });

  it("reports null progress when the browser cannot say how much is left", async () => {
    stubXhr();
    const { progress, result, xhr } = pending();

    xhr.upload.onprogress?.({ lengthComputable: false, loaded: 512, total: 0 } as ProgressEvent);
    xhr.finish(201, IMAGE);
    await result;

    expect(progress).toHaveBeenCalledWith(null);
  });

  it("carries the spec §66 code and the service's own message off a 400", async () => {
    stubXhr();
    const { result, xhr } = pending();
    xhr.finish(400, {
      code: "invalid_image",
      message: "The upload is not a JPEG or PNG image.",
      details: {},
    });

    const error = (await result.catch((caught: unknown) => caught)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(400);
    expect(error.code).toBe("invalid_image");
    // The message is written for a reader and names the rule that was broken.
    expect(error.serverMessage).toBe("The upload is not a JPEG or PNG image.");
  });

  it("carries Retry-After off a 429, exactly as the fetch path does", async () => {
    stubXhr();
    const { result, xhr } = pending();
    xhr.finish(429, { detail: "slow down" }, { "retry-after": "17" });

    const error = (await result.catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(429);
    expect(error.retryAfterSeconds).toBe(17);
  });

  it.each([409, 404])("surfaces a %d with no envelope", async (status) => {
    stubXhr();
    const { result, xhr } = pending();
    xhr.finish(status, "");

    const error = (await result.catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(status);
    expect(error.code).toBeUndefined();
  });

  it("reports a request that never reached the service with no status", async () => {
    stubXhr();
    const { result, xhr } = pending();
    xhr.onerror?.();

    const error = (await result.catch((caught: unknown) => caught)) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBeUndefined();
  });

  it("rejects a 201 whose body is not an image record", async () => {
    stubXhr();
    const { result, xhr } = pending();
    xhr.finish(201, { id: 7 });

    await expect(result).rejects.toBeInstanceOf(ApiError);
  });

  it("aborts when the caller's signal fires", async () => {
    stubXhr();
    const controller = new AbortController();
    const result = uploadImage({
      analysisId: ANALYSIS.id,
      side: "back",
      file: photograph(),
      signal: controller.signal,
    });
    const xhr = FakeXhr.last as FakeXhr;

    controller.abort();

    await expect(result).rejects.toBeInstanceOf(ApiError);
    expect(xhr.aborted).toBe(true);
  });

  it("does not open a request for an already-aborted signal", async () => {
    stubXhr();
    const result = uploadImage({
      analysisId: ANALYSIS.id,
      side: "back",
      file: photograph(),
      signal: AbortSignal.abort(),
    });

    await expect(result).rejects.toBeInstanceOf(ApiError);
    expect(FakeXhr.last).toBeUndefined();
  });
});
