import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CardConfirmation } from "@/app/identify/CardConfirmation";
import { rememberAnalysis } from "@/lib/analysis-session";
import { ApiError, type AnalysisResponse, type CardResponse } from "@/lib/api";

// `ApiError` stays real: the gate tells a missing card from an outage with
// `instanceof` and the spec §66 code, exactly as the detail view does.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getCard: vi.fn(),
  confirmCard: vi.fn(),
}));

let currentParams = new URLSearchParams();
// Recorded rather than stubbed away: a confirmation that was saved advances to
// the configuration screen (#66), and where it goes is the assertion.
const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => currentParams,
}));

const { getCard, confirmCard } = await import("@/lib/api");
const getCardMock = vi.mocked(getCard);
const confirmCardMock = vi.mocked(confirmCard);

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";

/** What `/analyze` leaves behind for this screen (#104). */
function withPhotographs(): void {
  rememberAnalysis(ANALYSIS_ID);
}

function confirmed(): AnalysisResponse {
  return {
    id: ANALYSIS_ID,
    status: "analyzing",
    created_at: "2026-08-21T00:00:00Z",
    completed_at: null,
    card_id: CARD_ID,
    images: [],
    // Spec §57's record. Nothing in the web app reads it (#40 is API-only),
    // but the field is required, so a fixture that omitted it would be a
    // response the service never sends.
    reproducibility: {
      application_version: null,
      model_bundle_version: null,
      card_database_version: null,
      grading_rules_version: null,
      market_snapshot_id: null,
      economic_configuration_id: null,
      image_sha256: {},
    },
  };
}

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
  // An analysis left over from a previous test would silently move every
  // confirmation onto the saved branch, so this resets with the mocks.
  window.sessionStorage.clear();
  confirmCardMock.mockReset();
  confirmCardMock.mockResolvedValue(confirmed());
  push.mockReset();
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

  it("is honest that a confirmation with no photographs is saved nowhere", async () => {
    // Arriving from the catalog with nothing uploaded is still a legitimate
    // path, and then there is no analysis to record anything against.
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.getByText(/no photographs in this tab/i)).toBeInTheDocument();
    expect(confirmCardMock).not.toHaveBeenCalled();
  });

  it("records the card against the analysis the tab is working on", async () => {
    getCardMock.mockResolvedValue(card());
    withPhotographs();

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));

    await screen.findByRole("heading", { name: /^Confirmed/ });
    expect(confirmCardMock).toHaveBeenCalledWith(ANALYSIS_ID, CARD_ID);
    expect(screen.getByText(/recorded as being of this card/i)).toBeInTheDocument();
  });

  it("says nothing is analysing the photographs yet", async () => {
    // The analysis rests in `analyzing` until the condition stages exist, so
    // this screen must not imply that work is under way.
    getCardMock.mockResolvedValue(card());
    withPhotographs();

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.getByText(/nothing has analysed them yet/i)).toBeInTheDocument();
  });

  it("leads on to the costs once the confirmation was recorded", async () => {
    // Spec §5 puts the economics behind a confirmed card, and #66 built the
    // screen. Without this link the step is reachable only by typing its URL.
    getCardMock.mockResolvedValue(card());
    withPhotographs();

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.getByRole("link", { name: "Set the costs" })).toHaveAttribute(
      "href",
      "/configure",
    );

    // And it goes there on its own, with the link live throughout for anyone
    // who would rather not wait. The pause is four seconds — long enough to
    // read the card back, which is the whole point of the gate — so this waits
    // past it rather than hurrying the component.
    await waitFor(() => expect(push).toHaveBeenCalledWith("/configure"), { timeout: 6_000 });
  }, 10_000);

  it("offers no way on when the confirmation was this page's alone", async () => {
    // No analysis in this tab means nothing to price: `/configure` would answer
    // with its own empty state, which is not a step forward.
    getCardMock.mockResolvedValue(card());

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));
    await screen.findByRole("heading", { name: /^Confirmed/ });

    expect(screen.queryByRole("link", { name: "Set the costs" })).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("does not claim a confirmation the service refused", async () => {
    getCardMock.mockResolvedValue(card());
    withPhotographs();
    confirmCardMock.mockRejectedValue(new ApiError("down", { status: undefined }));

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));

    await screen.findByRole("alert");
    expect(screen.queryByRole("heading", { name: /^Confirmed/ })).not.toBeInTheDocument();
    // Back to the question, with the tap still available.
    expect(screen.getByRole("button", { name: "Confirm this card" })).toBeEnabled();
  });

  it("waits rather than offering a button when throttled", async () => {
    getCardMock.mockResolvedValue(card());
    withPhotographs();
    confirmCardMock.mockRejectedValue(
      new ApiError("slow down", { status: 429, retryAfterSeconds: 30 }),
    );

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));

    await screen.findByRole("alert");
    // Pressing a retry would fire straight back into the limit (ADR 0005).
    expect(screen.queryByRole("button", { name: "Confirm this card" })).not.toBeInTheDocument();
    expect(screen.getByText(/30 more seconds/)).toBeInTheDocument();
  });

  it("stops offering the tap when there is nothing left to confirm against", async () => {
    getCardMock.mockResolvedValue(card());
    withPhotographs();
    confirmCardMock.mockRejectedValue(new ApiError("gone", { status: 404 }));

    render(<CardConfirmation />);
    fireEvent.click(await screen.findByRole("button", { name: "Confirm this card" }));

    await screen.findByRole("alert");
    expect(screen.getByText(/no longer available/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm this card" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Change card" })).toBeInTheDocument();
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
