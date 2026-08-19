import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardDetail } from "@/app/cards/[cardId]/CardDetail";
import { ApiError, type CardResponse } from "@/lib/api";

// `ApiError` stays real: the component tells a missing card from an outage with
// `instanceof` and the spec §66 code.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getCard: vi.fn(),
}));

const { getCard } = await import("@/lib/api");
const getCardMock = vi.mocked(getCard);

const CARD_ID = "22222222-2222-2222-2222-222222222222";

function card(overrides: Partial<CardResponse> = {}): CardResponse {
  return {
    id: CARD_ID,
    name: "Charizard",
    card_number: "4/102",
    game: "pokemon",
    language: "en",
    rarity: "Rare Holo",
    variant: "unlimited-holo",
    metadata: {},
    set: {
      id: "11111111-1111-1111-1111-111111111111",
      set_code: "BS",
      name: "Base Set",
      release_date: "1999-01-09",
      metadata: { total_cards: 102 },
    },
    external_ids: [{ provider: "manual", external_id: "bs-4-unlimited-holo" }],
    ...overrides,
  };
}

beforeEach(() => {
  getCardMock.mockReset();
});

describe("card detail", () => {
  it("reports that it is loading before the catalog answers", () => {
    getCardMock.mockReturnValue(new Promise(() => {}));

    render(<CardDetail cardId={CARD_ID} />);

    expect(screen.getByText("Looking this card up…")).toBeInTheDocument();
  });

  it("renders the card's canonical record", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByRole("heading", { name: "Charizard" })).toBeInTheDocument();
    expect(screen.getByText("Base Set (BS)")).toBeInTheDocument();
    expect(screen.getByText("4/102")).toBeInTheDocument();
    expect(screen.getByText("unlimited-holo")).toBeInTheDocument();
    expect(screen.getByText("Rare Holo")).toBeInTheDocument();
    expect(screen.getByText("English")).toBeInTheDocument();
    expect(screen.getByText("1999-01-09")).toBeInTheDocument();
  });

  it("renders a Japanese card in its own script", async () => {
    getCardMock.mockResolvedValue(
      card({
        name: "リザードン",
        language: "ja",
        variant: null,
        rarity: "R",
        set: {
          id: "33333333-3333-3333-3333-333333333333",
          set_code: "SV2a",
          name: "ポケモンカード151",
          release_date: "2023-06-16",
          metadata: {},
        },
      }),
    );

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByRole("heading", { name: "リザードン" })).toBeInTheDocument();
    expect(screen.getByText("ポケモンカード151 (SV2a)")).toBeInTheDocument();
    expect(screen.getByText("Japanese")).toBeInTheDocument();
  });

  it("states an unrecorded variant instead of leaving it blank", async () => {
    getCardMock.mockResolvedValue(card({ variant: null, rarity: null }));

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByText("variant not recorded")).toBeInTheDocument();
    expect(screen.getByText("rarity not recorded")).toBeInTheDocument();
  });

  it("lists every provider identifier, including two from one provider", async () => {
    // The index behind these is deliberately not unique (#23): a provider that
    // does not split printing variants issues one identifier for several cards.
    getCardMock.mockResolvedValue(
      card({
        external_ids: [
          { provider: "manual", external_id: "bs-4-unlimited-holo" },
          { provider: "example", external_id: "example-base-charizard" },
          { provider: "example", external_id: "example-base-charizard-alt" },
        ],
      }),
    );

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByText("bs-4-unlimited-holo")).toBeInTheDocument();
    expect(screen.getByText("example-base-charizard")).toBeInTheDocument();
    expect(screen.getByText("example-base-charizard-alt")).toBeInTheDocument();
    expect(screen.getAllByText("example")).toHaveLength(2);
  });

  it("says so when no provider records the card", async () => {
    getCardMock.mockResolvedValue(card({ external_ids: [] }));

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByText("No external provider records this card.")).toBeInTheDocument();
  });

  it("renders catalog metadata when there is any", async () => {
    getCardMock.mockResolvedValue(
      card({ metadata: { note: "Fictional.", printed_year: 1999, promo: false } }),
    );

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByText("Fictional.")).toBeInTheDocument();
    expect(screen.getByText("1999")).toBeInTheDocument();
    expect(screen.getByText("false")).toBeInTheDocument();
  });

  it("omits the metadata section when the card records none", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardDetail cardId={CARD_ID} />);

    await screen.findByRole("heading", { name: "Charizard" });
    expect(screen.queryByRole("heading", { name: "Catalog metadata" })).not.toBeInTheDocument();
  });

  it("shows a placeholder rather than a broken image", async () => {
    // ADR 0004: the licence covers the card data, not the artwork, so this
    // product imports none. The absence is stated, not left as an empty slot.
    getCardMock.mockResolvedValue(card());

    const { container } = render(<CardDetail cardId={CARD_ID} />);
    await screen.findByRole("heading", { name: "Charizard" });

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("No card image")).toBeInTheDocument();
    expect(screen.getByText(/only card images this product ever shows/i)).toBeInTheDocument();
  });

  it("offers no path from a card into analysis", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardDetail cardId={CARD_ID} />);
    await screen.findByRole("heading", { name: "Charizard" });

    expect(screen.queryByRole("link", { name: /analy/i })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /confirm|analy|use this card/i }),
    ).not.toBeInTheDocument();
  });

  it("promises neither a price nor a grade", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardDetail cardId={CARD_ID} />);

    expect(await screen.findByText(/not a valuation and not a grade/i)).toBeInTheDocument();
  });

  it("reports an identifier that names no card as missing, not as an outage", async () => {
    getCardMock.mockRejectedValue(
      new ApiError("gone", {
        status: 404,
        code: "card_not_identified",
        details: { card_id: CARD_ID },
      }),
    );

    render(<CardDetail cardId={CARD_ID} />);

    expect(
      await screen.findByRole("heading", { name: "No card is recorded under that identifier." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the search" })).toHaveAttribute(
      "href",
      "/cards",
    );
    // Retrying a card that does not exist would just fail again.
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("reports a malformed identifier as a link that leads nowhere", async () => {
    // FastAPI's own request-validation 422, which carries no §66 code. Offering
    // to retry it would be a lie: it fails identically every time.
    getCardMock.mockRejectedValue(new ApiError("malformed", { status: 422 }));

    render(<CardDetail cardId="not-a-uuid" />);

    expect(
      await screen.findByRole("heading", { name: "No card is recorded under that identifier." }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("reports an unreachable catalog as an outage", async () => {
    getCardMock.mockRejectedValue(new ApiError("down", { status: 503, code: "provider_error" }));

    render(<CardDetail cardId={CARD_ID} />);

    expect(
      await screen.findByRole("heading", { name: "The card catalog is unavailable right now." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("reports a request that never reached the server as an outage", async () => {
    getCardMock.mockRejectedValue(new ApiError("unreachable"));

    render(<CardDetail cardId={CARD_ID} />);

    expect(
      await screen.findByRole("heading", { name: "The card catalog is unavailable right now." }),
    ).toBeInTheDocument();
  });

  it("separates a payload it could not understand from an outage", async () => {
    getCardMock.mockRejectedValue(new ApiError("weird", { status: 200 }));

    render(<CardDetail cardId={CARD_ID} />);

    expect(
      await screen.findByRole("heading", { name: "This card could not be loaded." }),
    ).toBeInTheDocument();
  });

  it("fetches the card again when the user retries", async () => {
    getCardMock.mockRejectedValueOnce(new ApiError("down", { status: 503 }));
    getCardMock.mockResolvedValue(card());

    render(<CardDetail cardId={CARD_ID} />);

    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Charizard" })).toBeInTheDocument();
  });

  it("abandons the request when the user navigates away", async () => {
    getCardMock.mockResolvedValue(card());

    const { unmount } = render(<CardDetail cardId={CARD_ID} />);
    const [, signal] = getCardMock.mock.calls[0] as [unknown, AbortSignal];

    unmount();

    await waitFor(() => expect(signal.aborted).toBe(true));
  });
});
