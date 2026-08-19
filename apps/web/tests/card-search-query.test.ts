import { describe, expect, it } from "vitest";

import {
  EMPTY_QUERY,
  MAX_OFFSET,
  PAGE_SIZE,
  cardsHref,
  hasFilters,
  pageRange,
  parseSearchQuery,
  toApiParams,
  toUrlSearchParams,
} from "@/lib/card-search";

function query(search: string) {
  return parseSearchQuery(new URLSearchParams(search));
}

describe("parseSearchQuery", () => {
  it("reads every filter the UI exposes", () => {
    expect(query("text=charizard&card_number=4&set_code=BS&language=en&offset=20")).toEqual({
      text: "charizard",
      cardNumber: "4",
      setCode: "BS",
      language: "en",
      offset: 20,
    });
  });

  it("is the empty query when nothing is in the URL", () => {
    expect(query("")).toEqual(EMPTY_QUERY);
  });

  it("trims padded values", () => {
    // The route validates filters with the domain's own grammar, under which a
    // padded value is a 422. Trimming here turns a stray space into a search.
    expect(query("text=%20charizard%20&set_code=%20BS%20")).toMatchObject({
      text: "charizard",
      setCode: "BS",
    });
  });

  it("keeps a Japanese name intact", () => {
    expect(query(`text=${encodeURIComponent("リザードン")}`).text).toBe("リザードン");
  });

  it("drops a language that is not an ISO 639-1 code rather than forwarding it", () => {
    expect(query("language=english").language).toBe("");
    expect(query("language=e").language).toBe("");
  });

  it("lower-cases a language the user typed in capitals", () => {
    expect(query("language=EN").language).toBe("en");
  });

  it.each(["offset=abc", "offset=-5", "offset=", "offset=NaN", "offset=%20"])(
    "falls back to the first page for %s",
    (search) => {
      expect(query(search).offset).toBe(0);
    },
  );

  it("takes the leading integer of a decimal offset", () => {
    // `parseInt` semantics, kept deliberately: the URL is user-editable and a
    // near-miss should land on a page rather than be rejected.
    expect(query("offset=20.9").offset).toBe(20);
  });

  it("clamps an offset past the route's bound", () => {
    // Past `bigint` the driver overflows and the failure surfaces as a 503
    // blaming the catalog, which would be a lie about what went wrong.
    expect(query(`offset=${MAX_OFFSET + 1000}`).offset).toBe(MAX_OFFSET);
  });
});

describe("toUrlSearchParams", () => {
  it("omits every blank filter", () => {
    // `?text=` — present but empty — is a 422 from the route, not an unfiltered
    // search, so a blank field must leave the query string entirely.
    expect(toUrlSearchParams(EMPTY_QUERY).toString()).toBe("");
  });

  it("omits a filter that is only whitespace", () => {
    expect(toUrlSearchParams({ ...EMPTY_QUERY, text: "   " }).toString()).toBe("");
  });

  it("omits the first page's offset", () => {
    expect(toUrlSearchParams({ ...EMPTY_QUERY, text: "mew" }).toString()).toBe("text=mew");
  });

  it("carries an offset once it is off the first page", () => {
    expect(toUrlSearchParams({ ...EMPTY_QUERY, offset: 40 }).get("offset")).toBe("40");
  });

  it("round-trips a query through the URL unchanged", () => {
    const original = {
      text: "リザードン",
      cardNumber: "025",
      setCode: "SV2a",
      language: "ja",
      offset: 20,
    };

    expect(parseSearchQuery(toUrlSearchParams(original))).toEqual(original);
  });
});

describe("cardsHref", () => {
  it("is the bare route when nothing is filtered", () => {
    expect(cardsHref(EMPTY_QUERY)).toBe("/cards");
  });

  it("carries the filters otherwise", () => {
    expect(cardsHref({ ...EMPTY_QUERY, setCode: "BS" })).toBe("/cards?set_code=BS");
  });
});

describe("toApiParams", () => {
  it("maps the UI's names onto the endpoint's and pins the window", () => {
    expect(
      toApiParams({
        text: "charizard",
        cardNumber: "4",
        setCode: "BS",
        language: "en",
        offset: 20,
      }),
    ).toEqual({
      text: "charizard",
      card_number: "4",
      set_code: "BS",
      language: "en",
      limit: PAGE_SIZE,
      offset: 20,
    });
  });

  it("clamps the offset it sends", () => {
    expect(toApiParams({ ...EMPTY_QUERY, offset: MAX_OFFSET * 2 }).offset).toBe(MAX_OFFSET);
  });
});

describe("hasFilters", () => {
  it("is false for an unfiltered browse, at any offset", () => {
    expect(hasFilters(EMPTY_QUERY)).toBe(false);
    expect(hasFilters({ ...EMPTY_QUERY, offset: 40 })).toBe(false);
  });

  it.each([
    ["text", { text: "mew" }],
    ["cardNumber", { cardNumber: "25" }],
    ["setCode", { setCode: "BS" }],
    ["language", { language: "ja" }],
  ])("is true once %s narrows the search", (_name, narrowing) => {
    expect(hasFilters({ ...EMPTY_QUERY, ...narrowing })).toBe(true);
  });
});

describe("pageRange", () => {
  it("counts from one", () => {
    expect(pageRange(0, 20)).toEqual({ first: 1, last: 20 });
  });

  it("reports the last page's real length rather than the window size", () => {
    expect(pageRange(20, 2)).toEqual({ first: 21, last: 22 });
  });

  it("reports nothing for an empty page", () => {
    expect(pageRange(40, 0)).toEqual({ first: 0, last: 0 });
  });
});
