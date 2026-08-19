import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getCard, searchCards } from "@/lib/api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const SET = {
  id: "11111111-1111-1111-1111-111111111111",
  set_code: "BS",
  name: "Base Set",
  release_date: "1999-01-09",
  metadata: { total_cards: 102 },
};

const SUMMARY = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Charizard",
  card_number: "4/102",
  game: "pokemon",
  language: "en",
  rarity: "Rare Holo",
  variant: "unlimited-holo",
  set: SET,
};

const DETAIL = {
  ...SUMMARY,
  metadata: {},
  external_ids: [{ provider: "manual", external_id: "bs-4-unlimited-holo" }],
};

function stubFetch(response: Response | Error) {
  const mock =
    response instanceof Error
      ? vi.fn().mockRejectedValue(response)
      : vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** The URL of the single request the stub recorded. */
function requestedUrl(mock: ReturnType<typeof vi.fn>): URL {
  const [url] = mock.mock.calls[0] as [string];
  return new URL(url);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("searchCards", () => {
  it("returns the page", async () => {
    stubFetch(jsonResponse({ cards: [SUMMARY], total: 1, limit: 20, offset: 0 }));

    await expect(searchCards({ text: "charizard" })).resolves.toEqual({
      cards: [SUMMARY],
      total: 1,
      limit: 20,
      offset: 0,
    });
  });

  it("sends each filter under the endpoint's own parameter name", async () => {
    const fetchMock = stubFetch(jsonResponse({ cards: [], total: 0, limit: 20, offset: 0 }));

    await searchCards({ text: "charizard", set_code: "BS", card_number: "4", limit: 20 });

    const url = requestedUrl(fetchMock);
    expect(url.pathname).toBe("/cards/search");
    expect(url.searchParams.get("text")).toBe("charizard");
    expect(url.searchParams.get("set_code")).toBe("BS");
    expect(url.searchParams.get("card_number")).toBe("4");
    expect(url.searchParams.get("limit")).toBe("20");
  });

  it("omits a blank filter rather than sending it empty", async () => {
    // `?text=` is a 422 from the route's validation, not an unfiltered search.
    const fetchMock = stubFetch(jsonResponse({ cards: [], total: 0, limit: 20, offset: 0 }));

    await searchCards({ text: "", set_code: "   ", language: null, card_number: "MEW" });

    const url = requestedUrl(fetchMock);
    expect(url.searchParams.has("text")).toBe(false);
    expect(url.searchParams.has("set_code")).toBe(false);
    expect(url.searchParams.has("language")).toBe(false);
    expect(url.searchParams.get("card_number")).toBe("MEW");
  });

  it("sends a bare path when nothing is filtered", async () => {
    const fetchMock = stubFetch(jsonResponse({ cards: [], total: 0, limit: 20, offset: 0 }));

    await searchCards();

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url.endsWith("/cards/search")).toBe(true);
  });

  it("keeps a Japanese query readable on the wire", async () => {
    const fetchMock = stubFetch(jsonResponse({ cards: [], total: 0, limit: 20, offset: 0 }));

    await searchCards({ text: "リザードン" });

    expect(requestedUrl(fetchMock).searchParams.get("text")).toBe("リザードン");
  });

  it("carries the spec §66 code off a 503", async () => {
    stubFetch(
      jsonResponse(
        {
          code: "provider_error",
          message: "The card catalog could not be reached.",
          details: { reason: "catalog_unreachable" },
        },
        503,
      ),
    );

    await expect(searchCards()).rejects.toMatchObject({
      status: 503,
      code: "provider_error",
      details: { reason: "catalog_unreachable" },
    });
  });

  it("throws ApiError when the payload is not a search page", async () => {
    stubFetch(jsonResponse({ cards: [{ id: "only-an-id" }], total: 1, limit: 20, offset: 0 }));

    await expect(searchCards()).rejects.toBeInstanceOf(ApiError);
  });

  it("throws ApiError when the network fails", async () => {
    stubFetch(new TypeError("Failed to fetch"));

    await expect(searchCards()).rejects.toMatchObject({ status: undefined, code: undefined });
  });
});

describe("getCard", () => {
  it("returns the card", async () => {
    stubFetch(jsonResponse(DETAIL));

    await expect(getCard(DETAIL.id)).resolves.toEqual(DETAIL);
  });

  it("requests the card on the configured base URL", async () => {
    const fetchMock = stubFetch(jsonResponse(DETAIL));
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");

    await getCard(DETAIL.id);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`https://api.example.test/cards/${DETAIL.id}`);
    expect(init.cache).toBe("no-store");
  });

  it("carries card_not_identified off a 404", async () => {
    // The status is overridden from the taxonomy's 422 because the request was
    // well-formed; the code is the part that is contractual.
    stubFetch(
      jsonResponse(
        {
          code: "card_not_identified",
          message: "No card is recorded under that identifier.",
          details: { card_id: DETAIL.id },
        },
        404,
      ),
    );

    await expect(getCard(DETAIL.id)).rejects.toMatchObject({
      status: 404,
      code: "card_not_identified",
      details: { card_id: DETAIL.id },
    });
  });

  it("still raises cleanly when a failure carries no error envelope", async () => {
    // FastAPI's own request-validation 422 is `{detail: [...]}`, a different
    // shape, and a proxy may return no JSON at all. Neither may become a crash.
    stubFetch(new Response("<html>502</html>", { status: 502 }));

    const error = await getCard(DETAIL.id).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 502, code: undefined });
  });

  it("throws ApiError when the payload is missing the detail-only fields", async () => {
    // A summary is not a card detail: it carries no metadata and no providers.
    stubFetch(jsonResponse(SUMMARY));

    await expect(getCard(SUMMARY.id)).rejects.toBeInstanceOf(ApiError);
  });

  it("throws ApiError when the body is not JSON", async () => {
    stubFetch(new Response("not json", { status: 200 }));

    await expect(getCard(DETAIL.id)).rejects.toBeInstanceOf(ApiError);
  });
});
