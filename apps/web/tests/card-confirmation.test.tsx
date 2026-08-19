import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardConfirmation } from "@/app/identify/CardConfirmation";
import { ApiError, type CardResponse } from "@/lib/api";

// `ApiError` stays real: the gate tells a missing card from an outage with
// `instanceof` and the spec §66 code, exactly as the detail view does.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getCard: vi.fn(),
}));

let currentParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useSearchParams: () => currentParams,
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
  // A card_id leaking from a previous test silently changes which branch
  // renders, so both of these reset together.
  currentParams = new URLSearchParams(`card_id=${CARD_ID}`);
  getCardMock.mockReset();
});

describe("the confirmation gate", () => {
  it("reports that it is loading before the catalog answers", () => {
    getCardMock.mockReturnValue(new Promise(() => {}));

    render(<CardConfirmation />);

    expect(screen.getByText("Looking this card up…")).toBeInTheDocument();
  });

  it("asks rather than asserts, and shows what the card is", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);

    expect(
      await screen.findByRole("heading", { name: "Is this the card you are holding?" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Charizard" })).toBeInTheDocument();
    expect(screen.getByText("Base Set · BS 4/102")).toBeInTheDocument();
    expect(screen.getByText("Base Set (BS)")).toBeInTheDocument();
    expect(screen.getByText("4/102")).toBeInTheDocument();
    expect(screen.getByText("unlimited-holo")).toBeInTheDocument();
    expect(screen.getByText("Rare Holo")).toBeInTheDocument();
    expect(screen.getByText("English")).toBeInTheDocument();
  });

  it("renders InsufficientInformation as an explicit unknown, not as zero", async () => {
    // The issue's third required test. 0% is a measurement saying the card is
    // certainly not this one; no measurement was taken.
    getCardMock.mockResolvedValue(card());

    const { container } = render(<CardConfirmation />);
    await screen.findByRole("heading", { name: "Charizard" });

    expect(screen.getByText("Identification confidence")).toBeInTheDocument();
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
    expect(container.textContent).not.toContain("%");
    expect(screen.getByText(/nothing has identified this card/i)).toBeInTheDocument();
  });

  it("does not present an unidentified card as settled", async () => {
    // The issue's second required test. There is no auto-confirm at any
    // confidence, so the screen arrives awaiting an answer and Change carries
    // the same weight as Confirm while nothing has been measured.
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);
    const confirm = await screen.findByRole("button", { name: "Confirm this card" });

    expect(confirm).toBeEnabled();
    expect(screen.queryByRole("heading", { name: /^Confirmed/ })).not.toBeInTheDocument();
    expect(confirm.parentElement).toHaveAttribute("data-certainty", "unknown");
  });

  it("confirms only on the user's tap, and then says so", async () => {
    // The issue's first required test, first half.
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));

    const heading = await screen.findByRole("heading", {
      name: "Confirmed: this is the card you are holding.",
    });
    expect(heading).toBeInTheDocument();
    expect(heading).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Confirm this card" })).not.toBeInTheDocument();
    // The record shown is the record confirmed.
    expect(screen.getByRole("heading", { name: "Charizard" })).toBeInTheDocument();
    expect(screen.getByText("unlimited-holo")).toBeInTheDocument();
    // Confirming must not refetch, or a late answer could clobber the answer.
    expect(getCardMock).toHaveBeenCalledTimes(1);
  });

  it("is honest that a confirmation is not saved anywhere", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.getByText(/it is not saved/i)).toBeInTheDocument();
    expect(screen.getByText(/arrive in M2/i)).toBeInTheDocument();
  });

  it("routes Change back into the catalog search, before and after confirming", async () => {
    // The issue's first required test, second half.
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);

    expect(await screen.findByRole("link", { name: "Change card" })).toHaveAttribute(
      "href",
      "/cards",
    );

    fireEvent.click(screen.getByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.getByRole("link", { name: "Start over with another card" })).toHaveAttribute(
      "href",
      "/cards",
    );
  });

  it("never becomes a door into analysis", async () => {
    getCardMock.mockResolvedValue(card());

    const { container } = render(<CardConfirmation />);
    await screen.findByRole("heading", { name: "Charizard" });

    expect(screen.queryByRole("link", { name: /analy/i })).not.toBeInTheDocument();
    expect(container.querySelector('a[href^="/analyze"]')).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.queryByRole("link", { name: /analy/i })).not.toBeInTheDocument();
    expect(container.querySelector('a[href^="/analyze"]')).toBeNull();
  });

  it("shows a placeholder rather than a broken image", async () => {
    getCardMock.mockResolvedValue(card());

    const { container } = render(<CardConfirmation />);
    await screen.findByRole("heading", { name: "Charizard" });

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("No card image")).toBeInTheDocument();
    expect(screen.getByText(/the facts below are what you check against/i)).toBeInTheDocument();
  });

  it("says an unrecorded variant out loud, in the same words as the catalog", async () => {
    getCardMock.mockResolvedValue(card({ variant: null, rarity: null }));

    render(<CardConfirmation />);

    expect(await screen.findByText("variant not recorded")).toBeInTheDocument();
    expect(screen.getByText("rarity not recorded")).toBeInTheDocument();
  });

  it("links back to the card's full catalog record", async () => {
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);

    expect(await screen.findByRole("link", { name: /full catalog record/i })).toHaveAttribute(
      "href",
      `/cards/${CARD_ID}`,
    );
  });

  it("says so plainly when no card has been selected, and asks the catalog nothing", async () => {
    currentParams = new URLSearchParams();

    render(<CardConfirmation />);

    expect(screen.getByRole("heading", { name: "No card is selected." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Find a card" })).toHaveAttribute("href", "/cards");
    expect(getCardMock).not.toHaveBeenCalled();
  });

  it("treats a blank card_id the same as an absent one", () => {
    currentParams = new URLSearchParams("card_id=");

    render(<CardConfirmation />);

    expect(screen.getByRole("heading", { name: "No card is selected." })).toBeInTheDocument();
    expect(getCardMock).not.toHaveBeenCalled();
  });

  it("reports an identifier that names no card as missing, with nothing to retry", async () => {
    getCardMock.mockRejectedValue(
      new ApiError("gone", { status: 404, code: "card_not_identified" }),
    );

    render(<CardConfirmation />);

    expect(
      await screen.findByRole("heading", { name: "No card is recorded under that identifier." }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the search" })).toHaveAttribute(
      "href",
      "/cards",
    );
  });

  it("reports a malformed identifier as missing too", async () => {
    currentParams = new URLSearchParams("card_id=not-a-uuid");
    getCardMock.mockRejectedValue(new ApiError("unprocessable", { status: 422 }));

    render(<CardConfirmation />);

    expect(
      await screen.findByRole("heading", { name: "No card is recorded under that identifier." }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("reports an unreachable catalog as an outage, and retries it", async () => {
    getCardMock.mockRejectedValueOnce(
      new ApiError("down", {
        status: 503,
        code: "provider_error",
        details: { reason: "catalog_unreachable" },
      }),
    );

    render(<CardConfirmation />);

    expect(
      await screen.findByRole("heading", { name: "The card catalog is unavailable right now." }),
    ).toBeInTheDocument();

    getCardMock.mockResolvedValue(card());
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", { name: "Is this the card you are holding?" }),
    ).toBeInTheDocument();
  });

  it("keeps an answer it did not understand separate from an outage", async () => {
    getCardMock.mockRejectedValue(new ApiError("odd", { status: 500, code: "internal_error" }));

    render(<CardConfirmation />);

    expect(
      await screen.findByRole("heading", { name: "This card could not be loaded." }),
    ).toBeInTheDocument();
  });

  it("abandons the request when the screen goes away", async () => {
    getCardMock.mockReturnValue(new Promise(() => {}));

    const { unmount } = render(<CardConfirmation />);
    const [, signal] = getCardMock.mock.calls[0] as [string, AbortSignal];

    unmount();

    await waitFor(() => expect(signal.aborted).toBe(true));
  });

  it("drops a confirmation that belonged to a different card", async () => {
    // A confirmation is about one card. Pointing the screen at another one has
    // to return it to the question, not carry the answer across.
    getCardMock.mockResolvedValue(card());

    const { rerender } = render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    const other = "33333333-3333-3333-3333-333333333333";
    currentParams = new URLSearchParams(`card_id=${other}`);
    getCardMock.mockResolvedValue(card({ id: other, name: "Blastoise", card_number: "2/102" }));
    rerender(<CardConfirmation />);

    expect(
      await screen.findByRole("heading", { name: "Is this the card you are holding?" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Blastoise" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^Confirmed/ })).not.toBeInTheDocument();
  });
});
