import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EconomicConfiguration } from "@/app/configure/EconomicConfiguration";
import { rememberAnalysis } from "@/lib/analysis-session";
import {
  ApiError,
  type EconomicConfigurationRequest,
  type EconomicConfigurationResponse,
  type GradingCompaniesResponse,
} from "@/lib/api";

// `ApiError` stays real: this screen tells a second configuration from a
// throttled connection from an outage by its `status` and spec §66 `code`, and
// a fake would not exercise that.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getGradingCompanies: vi.fn(),
  configureEconomics: vi.fn(),
}));

const { getGradingCompanies, configureEconomics } = await import("@/lib/api");
const getGradingCompaniesMock = vi.mocked(getGradingCompanies);
const configureEconomicsMock = vi.mocked(configureEconomics);

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";

/** The three companies, spelled the way `GET /grading-companies` spells them. */
function companies(): GradingCompaniesResponse {
  return {
    companies: [
      { company: "psa", display_name: "PSA", grades: ["1", "1.5", "10"], rules: null },
      { company: "tag", display_name: "TAG", grades: ["1", "1.5", "10"], rules: null },
      // BGS is the one with a 9.5, which is why nothing here shares a scale.
      { company: "bgs", display_name: "BGS", grades: ["1", "9.5", "10"], rules: null },
    ],
  };
}

/**
 * What the endpoint answers. The costs come back filled in **whether or not the
 * user typed any** — that is where the standard figures come from, and it is
 * why `apps/web` does not know them.
 */
function stored(
  overrides: Partial<EconomicConfigurationResponse> = {},
): EconomicConfigurationResponse {
  return {
    id: "55555555-5555-5555-5555-555555555555",
    created_at: "2026-08-27T00:00:00Z",
    currency: "SGD",
    acquisition_cost: null,
    costs: {
      grading_fee: "40.00",
      outbound_shipping: "30.00",
      return_shipping: "30.00",
      insurance: "0.00",
      miscellaneous: "0.00",
      selling_fee: { rate: "0.1000", flat: "0.00" },
    },
    grading_companies: ["psa", "tag", "bgs"],
    optimization_mode: "expected_profit",
    // Stored and reported, never accepted: no client gates its own
    // recommendation (#65).
    thresholds: {
      minimum_image_quality: 0.5,
      minimum_grade_confidence: 0.5,
      minimum_figure_confidence: 0.4,
      maximum_unpriced_probability: 0.25,
      minimum_incremental_profit: "5.00",
    },
    ...overrides,
  };
}

function submitButton(): HTMLElement {
  return screen.getByRole("button", { name: /Use these figures|Recording…/ });
}

/** The request the form built, as it would have reached the wire. */
function sentRequest(): EconomicConfigurationRequest {
  const call = configureEconomicsMock.mock.calls[0];
  expect(call).toBeDefined();
  return call![1];
}

/** Render with photographs in this tab and the companies already answered. */
async function form(): Promise<void> {
  render(<EconomicConfiguration />);
  await screen.findByRole("button", { name: "Use these figures" });
}

beforeEach(() => {
  // An analysis left over from a previous test would silently move every render
  // off the "nothing in this tab" branch, so this resets with the mocks.
  window.sessionStorage.clear();
  rememberAnalysis(ANALYSIS_ID);
  getGradingCompaniesMock.mockReset();
  getGradingCompaniesMock.mockResolvedValue(companies());
  configureEconomicsMock.mockReset();
  configureEconomicsMock.mockResolvedValue(stored());
});

describe("what you paid", () => {
  it("submits null when the acquisition cost is left blank, never zero", async () => {
    // The issue's first required test, and spec §45's rule. A field that
    // defaulted to 0 would turn "I don't remember" into "it was free" and
    // report an investment return computed from a price nobody paid.
    await form();

    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().acquisition_cost).toBeNull();
  });

  it("submits 0.00 as a real acquisition cost when that is what was typed", async () => {
    // A raffle win or somebody else's pull. `null` and `"0.00"` reach different
    // answers, which is the whole reason blank is not zero.
    await form();

    fireEvent.change(screen.getByLabelText(/Acquisition cost/), { target: { value: "0.00" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().acquisition_cost).toBe("0.00");
  });

  it("sends the amount as a decimal string rather than a number", async () => {
    // The service refuses a JSON number where an amount is meant: it is a
    // binary float in most clients, and money must stay exact.
    await form();

    fireEvent.change(screen.getByLabelText(/Acquisition cost/), { target: { value: "120.50" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().acquisition_cost).toBe("120.50");
    expect(typeof sentRequest().acquisition_cost).toBe("string");
  });

  it("names the two questions the acquisition cost stands between", async () => {
    // Spec §45: the market grading decision and the investment return must be
    // distinguished rather than conflated, in the user's language.
    await form();

    expect(screen.getByText("Is it worth grading this card?")).toBeInTheDocument();
    expect(screen.getByText("Did this card make money?")).toBeInTheDocument();
  });

  it("refuses a malformed amount beside the field instead of sending it", async () => {
    await form();

    fireEvent.change(screen.getByLabelText(/Acquisition cost/), { target: { value: "-5" } });
    fireEvent.click(submitButton());

    expect(await screen.findByText(/at most two decimal places/)).toBeInTheDocument();
    expect(configureEconomicsMock).not.toHaveBeenCalled();
  });
});

describe("the optimization mode", () => {
  it("offers all five modes, each one explained", async () => {
    // The issue's second required test. "Best chance of the top grade" sounds
    // like the obviously right answer until it names a company that loses money.
    await form();

    for (const label of [
      "Most money made",
      "Best return on what you risk",
      "Best chance of the top grade",
      "Cheapest to submit",
      "Highest value once graded",
    ]) {
      expect(screen.getByRole("radio", { name: new RegExp(label) })).toBeInTheDocument();
    }

    expect(screen.getByText(/does not look at money at all/)).toBeInTheDocument();
    expect(screen.getByText(/does not look at what the card is worth/)).toBeInTheDocument();
  });

  it("sends the slug of whichever mode was chosen", async () => {
    await form();

    fireEvent.click(screen.getByRole("radio", { name: /Cheapest to submit/ }));
    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().optimization_mode).toBe("lowest_total_cost");
  });

  it("defaults to expected_profit rather than to nothing", async () => {
    await form();

    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().optimization_mode).toBe("expected_profit");
  });
});

describe("the companies", () => {
  it("lists what the service says, not a hard-coded set", async () => {
    // Grade scales and company metadata come from `GET /grading-companies`;
    // PSA and TAG have no 9.5 and BGS does, so a shared scale spelled here
    // would misrender one of them.
    getGradingCompaniesMock.mockResolvedValue({
      companies: [{ company: "cgc", display_name: "CGC", grades: ["1", "10"], rules: null }],
    });

    await form();

    expect(screen.getByRole("checkbox", { name: /CGC/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /PSA/ })).not.toBeInTheDocument();
  });

  it("sends every company still ticked", async () => {
    // The issue's third required test.
    await form();

    fireEvent.click(screen.getByRole("checkbox", { name: /TAG/ }));
    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().grading_companies).toEqual(["psa", "bgs"]);
  });

  it("refuses to submit with none of them ticked", async () => {
    await form();

    for (const name of [/PSA/, /TAG/, /BGS/]) {
      fireEvent.click(screen.getByRole("checkbox", { name }));
    }
    fireEvent.click(submitButton());

    expect(await screen.findByText("Choose at least one company to compare.")).toBeInTheDocument();
    expect(configureEconomicsMock).not.toHaveBeenCalled();
  });
});

describe("the costs", () => {
  it("sends no costs at all when none were typed", async () => {
    // The issue's fourth required test, and #65's binding: the defaults live in
    // the engine and `apps/web` must not carry a second copy. Omission is how
    // they are asked for.
    await form();

    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest()).not.toHaveProperty("costs");
  });

  it("sends only the line items that were overridden", async () => {
    await form();

    fireEvent.change(screen.getByLabelText("Grading fee"), { target: { value: "55.00" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().costs).toEqual({ grading_fee: "55.00" });
  });

  it("sends a selling fee typed as a percentage as the proportion the API wants", async () => {
    // The engine refuses `Decimal("10")` by name — ten percent is 0.10 — so the
    // field is a percentage and the shift happens here.
    await form();

    fireEvent.change(screen.getByLabelText("Selling fee"), { target: { value: "12.5" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(configureEconomicsMock).toHaveBeenCalled());
    expect(sentRequest().costs).toEqual({ selling_fee: { rate: "0.1250" } });
  });

  it("refuses a fee that would take more than the whole sale", async () => {
    await form();

    fireEvent.change(screen.getByLabelText("Selling fee"), { target: { value: "150" } });
    fireEvent.click(submitButton());

    expect(await screen.findByText(/between 0 and 100/)).toBeInTheDocument();
    expect(configureEconomicsMock).not.toHaveBeenCalled();
  });

  it("starts the cost fields blank rather than pre-filled with figures of its own", async () => {
    await form();

    for (const label of ["Grading fee", "Shipping to the grader", "Shipping back", "Insurance"]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
  });
});

describe("once the figures are recorded", () => {
  it("shows the amounts the service actually used, including the defaults", async () => {
    // This is the only place the standard costs are ever seen, and they are read
    // off the response — which is what lets the screen show them without
    // knowing them.
    await form();

    fireEvent.click(submitButton());

    expect(
      await screen.findByRole("heading", { name: "These are the figures the analysis will use." }),
    ).toBeInTheDocument();
    expect(screen.getByText("SGD 40.00")).toBeInTheDocument();
    expect(screen.getAllByText("SGD 30.00")).toHaveLength(2);
    expect(screen.getByText("10% of the sale")).toBeInTheDocument();
  });

  it("never presents the line items as a total", async () => {
    // #58: costs are named line items and never a total. §47's later dimensions
    // attach to individual lines, so a sum is a figure that has to be unpicked.
    await form();

    fireEvent.click(submitButton());

    await screen.findByRole("heading", { name: "These are the figures the analysis will use." });
    expect(screen.queryByText(/total/i)).not.toBeInTheDocument();
    // 40 + 30 + 30 + 0 + 0, had anything been tempted to add them up.
    expect(screen.queryByText("SGD 100.00")).not.toBeInTheDocument();
  });

  it("says an absent acquisition cost is absent, and what that costs the user", async () => {
    await form();

    fireEvent.click(submitButton());

    expect(await screen.findByText("Not supplied")).toBeInTheDocument();
    expect(screen.getByText(/not whether buying it made money/)).toBeInTheDocument();
  });

  it("does not offer to send them a second time", async () => {
    // #65: the configuration is written once and is immutable.
    await form();

    fireEvent.click(submitButton());

    await screen.findByRole("heading", { name: "These are the figures the analysis will use." });
    expect(screen.queryByRole("button", { name: /Use these figures/ })).not.toBeInTheDocument();
  });
});

describe("when something goes wrong", () => {
  it("treats a second configuration as final rather than offering another go", async () => {
    configureEconomicsMock.mockRejectedValue(new ApiError("conflict", { status: 409 }));

    await form();
    fireEvent.click(submitButton());

    expect(await screen.findByText(/already recorded/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Use these figures/ })).not.toBeInTheDocument();
  });

  it("counts a throttled submission down instead of offering a button", async () => {
    // ADR 0005: a button here fires straight back into the limit.
    configureEconomicsMock.mockRejectedValue(
      new ApiError("throttled", { status: 429, retryAfterSeconds: 30 }),
    );

    await form();
    fireEvent.click(submitButton());

    expect(await screen.findByText(/paused for 30 more seconds/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Use these figures/ })).not.toBeInTheDocument();
  });

  it("keeps the form and what was typed when the service is unreachable", async () => {
    configureEconomicsMock.mockRejectedValue(
      new ApiError("down", { status: 503, code: "provider_error" }),
    );

    await form();
    fireEvent.change(screen.getByLabelText(/Acquisition cost/), { target: { value: "120.00" } });
    fireEvent.click(submitButton());

    expect(await screen.findByText(/not answering/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Acquisition cost/)).toHaveValue("120.00");
    expect(screen.getByRole("button", { name: "Use these figures" })).toBeEnabled();
  });

  it("waits for the real company list rather than guessing at one", async () => {
    getGradingCompaniesMock.mockRejectedValue(
      new ApiError("down", { status: 503, code: "provider_error" }),
    );

    render(<EconomicConfiguration />);

    expect(
      await screen.findByRole("heading", { name: /grading companies could not be listed/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says so when this tab has no analysis, rather than taking figures it cannot store", async () => {
    window.sessionStorage.clear();

    render(<EconomicConfiguration />);

    expect(
      await screen.findByRole("heading", { name: "There is no card to price in this tab." }),
    ).toBeInTheDocument();
    expect(getGradingCompaniesMock).not.toHaveBeenCalled();
  });

  it("abandons a company listing the user has navigated away from", async () => {
    getGradingCompaniesMock.mockReturnValue(new Promise(() => {}));

    const { unmount } = render(<EconomicConfiguration />);
    const [signal] = getGradingCompaniesMock.mock.calls[0] as [AbortSignal];
    unmount();

    await waitFor(() => expect(signal.aborted).toBe(true));
  });
});
