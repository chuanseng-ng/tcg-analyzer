import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardSearch } from "@/app/cards/CardSearch";
import { ApiError, type CardSearchResponse, type CardSummaryResponse } from "@/lib/api";

const push = vi.fn();
let currentParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => currentParams,
}));

// `ApiError` stays real: the component tells an outage from a bug with
// `instanceof` and the spec §66 code, and a fake would not exercise that.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  searchCards: vi.fn(),
}));

const { searchCards } = await import("@/lib/api");
const searchCardsMock = vi.mocked(searchCards);

const BASE_SET = {
  id: "11111111-1111-1111-1111-111111111111",
  set_code: "BS",
  name: "Base Set",
  release_date: "1999-01-09",
  metadata: {},
};

const SV2A = {
  id: "33333333-3333-3333-3333-333333333333",
  set_code: "SV2a",
  name: "ポケモンカード151",
  release_date: "2023-06-16",
  metadata: {},
};

function summary(overrides: Partial<CardSummaryResponse> = {}): CardSummaryResponse {
  return {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Charizard",
    card_number: "4/102",
    game: "pokemon",
    language: "en",
    rarity: "Rare Holo",
    variant: "unlimited-holo",
    set: BASE_SET,
    ...overrides,
  };
}

function page(
  cards: CardSummaryResponse[],
  overrides: Partial<CardSearchResponse> = {},
): CardSearchResponse {
  return { cards, total: cards.length, limit: 20, offset: 0, ...overrides };
}

function resolvesWith(result: CardSearchResponse) {
  searchCardsMock.mockResolvedValue(result);
}

beforeEach(() => {
  currentParams = new URLSearchParams();
  push.mockReset();
  searchCardsMock.mockReset();
});

describe("card search", () => {
  it("reports that it is searching before the catalog answers", () => {
    searchCardsMock.mockReturnValue(new Promise(() => {}));

    render(<CardSearch />);

    expect(screen.getByText("Searching the catalog…")).toBeInTheDocument();
  });

  it("searches by name and renders the matches", async () => {
    currentParams = new URLSearchParams("text=charizard");
    resolvesWith(page([summary()]));

    render(<CardSearch />);

    expect(await screen.findByText("Showing 1–1 of 1")).toBeInTheDocument();
    expect(searchCardsMock).toHaveBeenCalledWith(
      expect.objectContaining({ text: "charizard" }),
      expect.anything(),
    );
  });

  it("searches by set and by card number", async () => {
    currentParams = new URLSearchParams("set_code=BS&card_number=4");
    resolvesWith(page([summary()]));

    render(<CardSearch />);

    await screen.findByText("Showing 1–1 of 1");
    expect(searchCardsMock).toHaveBeenCalledWith(
      expect.objectContaining({ set_code: "BS", card_number: "4" }),
      expect.anything(),
    );
  });

  it("searches with a Japanese name", async () => {
    currentParams = new URLSearchParams(`text=${encodeURIComponent("リザードン")}`);
    resolvesWith(
      page([
        summary({
          id: "44444444-4444-4444-4444-444444444444",
          name: "リザードン",
          card_number: "006/165",
          language: "ja",
          rarity: "R",
          variant: null,
          set: SV2A,
        }),
      ]),
    );

    render(<CardSearch />);

    expect(await screen.findByText("リザードン")).toBeInTheDocument();
    expect(searchCardsMock).toHaveBeenCalledWith(
      expect.objectContaining({ text: "リザードン" }),
      expect.anything(),
    );
  });

  it("distinguishes printing variants of the same card", async () => {
    // The economically load-bearing assertion. Holo, reverse holo and 1st
    // edition are different cards, there is no artwork to tell them apart by
    // (ADR 0004), and picking the wrong one is a wrong valuation.
    currentParams = new URLSearchParams("text=charizard&set_code=BS");
    resolvesWith(
      page([
        summary({ id: "aaaaaaaa-0000-0000-0000-000000000001", variant: "1st-edition-holo" }),
        summary({ id: "aaaaaaaa-0000-0000-0000-000000000002", variant: "shadowless-holo" }),
        summary({ id: "aaaaaaaa-0000-0000-0000-000000000003", variant: "unlimited-holo" }),
      ]),
    );

    render(<CardSearch />);

    const rows = await screen.findAllByRole("link", { name: /Charizard/ });
    const names = rows.map((row) => row.textContent ?? "");

    expect(names.filter((name) => name.includes("1st-edition-holo"))).toHaveLength(1);
    expect(names.filter((name) => name.includes("shadowless-holo"))).toHaveLength(1);
    expect(names.filter((name) => name.includes("unlimited-holo"))).toHaveLength(1);
    expect(new Set(rows.map((row) => row.getAttribute("href"))).size).toBe(3);
  });

  it("says so when a card records no variant, rather than leaving a gap", async () => {
    resolvesWith(page([summary({ variant: null, rarity: null })]));

    render(<CardSearch />);

    expect(await screen.findByText("variant not recorded")).toBeInTheDocument();
    expect(screen.getByText("rarity not recorded")).toBeInTheDocument();
  });

  it("routes each result to its own detail page", async () => {
    resolvesWith(page([summary()]));

    render(<CardSearch />);

    const row = await screen.findByRole("link", { name: /Charizard/ });
    expect(row).toHaveAttribute("href", "/cards/22222222-2222-2222-2222-222222222222");
  });

  it("offers no forward action from the results list itself", async () => {
    // Confirming which card is in the user's hand is a product-integrity gate
    // of its own (#91), and it is reached from a card's own page — after the
    // user has looked at the variant and number closely enough to be sure.
    // A confirm affordance on a row would let someone commit to a card from a
    // list, which is exactly what the gate exists to prevent. Nothing here
    // leads to analysis either.
    resolvesWith(page([summary()]));

    render(<CardSearch />);
    await screen.findByRole("link", { name: /Charizard/ });

    expect(
      screen.queryByRole("link", { name: /analy|confirm|this is my card/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /confirm|analy/i })).not.toBeInTheDocument();
  });

  it("puts a submitted search into the URL, omitting the fields left blank", async () => {
    resolvesWith(page([]));

    render(<CardSearch />);

    fireEvent.change(screen.getByLabelText("Card name"), { target: { value: "  pikachu  " } });
    fireEvent.change(screen.getByLabelText("Card number"), { target: { value: "25" } });
    fireEvent.submit(screen.getByRole("search"));

    expect(push).toHaveBeenCalledWith("/cards?text=pikachu&card_number=25");
  });

  it("returns to the first page when a new search is submitted", async () => {
    currentParams = new URLSearchParams("text=charizard&offset=40");
    resolvesWith(page([], { offset: 40 }));

    render(<CardSearch />);

    fireEvent.change(screen.getByLabelText("Card name"), { target: { value: "pikachu" } });
    fireEvent.submit(screen.getByRole("search"));

    expect(push).toHaveBeenCalledWith("/cards?text=pikachu");
  });

  it("shows the search that is on screen in the form's fields", async () => {
    currentParams = new URLSearchParams("text=charizard&set_code=BS&language=ja");
    resolvesWith(page([]));

    render(<CardSearch />);

    expect(screen.getByLabelText("Card name")).toHaveValue("charizard");
    expect(screen.getByLabelText("Set code")).toHaveValue("BS");
    expect(screen.getByLabelText("Language")).toHaveValue("ja");
  });

  it("treats nothing matching as an empty result, not an error", async () => {
    currentParams = new URLSearchParams("text=nothingmatchesthis");
    resolvesWith(page([]));

    render(<CardSearch />);

    expect(await screen.findByText("No cards match this search.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Clear the filters" })).toHaveAttribute(
      "href",
      "/cards",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("treats an offset past the end as an empty page too", async () => {
    currentParams = new URLSearchParams("offset=40");
    resolvesWith(page([], { total: 22, offset: 40 }));

    render(<CardSearch />);

    expect(await screen.findByText("No cards match this search.")).toBeInTheDocument();
  });

  it("distinguishes an empty catalog from filters that matched nothing", async () => {
    resolvesWith(page([]));

    render(<CardSearch />);

    expect(await screen.findByText("The catalog has no cards in it yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Clear the filters" })).not.toBeInTheDocument();
  });

  it("reports an unreachable catalog as an outage rather than a bad search", async () => {
    searchCardsMock.mockRejectedValue(
      new ApiError("boom", { status: 503, code: "provider_error" }),
    );

    render(<CardSearch />);

    expect(
      await screen.findByText("The card catalog is unavailable right now."),
    ).toBeInTheDocument();
  });

  it("reports a request that never reached the server as an outage", async () => {
    searchCardsMock.mockRejectedValue(new ApiError("unreachable"));

    render(<CardSearch />);

    expect(
      await screen.findByText("The card catalog is unavailable right now."),
    ).toBeInTheDocument();
  });

  it("does not offer to retry a payload the page could not understand", async () => {
    searchCardsMock.mockRejectedValue(new ApiError("weird", { status: 200 }));

    render(<CardSearch />);

    expect(await screen.findByText("That search could not be completed.")).toBeInTheDocument();
  });

  it("runs the search again when the user retries", async () => {
    searchCardsMock.mockRejectedValueOnce(new ApiError("boom", { status: 503 }));
    resolvesWith(page([summary()]));

    render(<CardSearch />);

    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("link", { name: /Charizard/ })).toBeInTheDocument();
  });

  it("pages forward when there are further matches", async () => {
    resolvesWith(page([summary()], { total: 22, offset: 0 }));

    render(<CardSearch />);

    expect(await screen.findByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/cards?offset=1",
    );
    expect(screen.queryByRole("link", { name: "Previous" })).not.toBeInTheDocument();
  });

  it("pages back, and offers no next page on the last one", async () => {
    currentParams = new URLSearchParams("offset=20");
    resolvesWith(page([summary()], { total: 21, offset: 20 }));

    render(<CardSearch />);

    expect(await screen.findByRole("link", { name: "Previous" })).toHaveAttribute("href", "/cards");
    expect(screen.queryByRole("link", { name: "Next" })).not.toBeInTheDocument();
  });

  it("keeps the filters when paging", async () => {
    currentParams = new URLSearchParams("text=charizard");
    resolvesWith(page([summary()], { total: 22, offset: 0 }));

    render(<CardSearch />);

    expect(await screen.findByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/cards?text=charizard&offset=1",
    );
  });

  it("shows no paging when everything fits on one page", async () => {
    resolvesWith(page([summary()]));

    render(<CardSearch />);

    await screen.findByRole("link", { name: /Charizard/ });
    expect(screen.queryByRole("navigation", { name: "Result pages" })).not.toBeInTheDocument();
  });

  it("abandons a search the user has navigated away from", async () => {
    resolvesWith(page([summary()]));

    const { unmount } = render(<CardSearch />);
    const [, signal] = searchCardsMock.mock.calls[0] as [unknown, AbortSignal];

    unmount();

    await waitFor(() => expect(signal.aborted).toBe(true));
  });
});
