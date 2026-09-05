import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Results } from "@/app/results/Results";
import { rememberAnalysis } from "@/lib/analysis-session";
import {
  ApiError,
  type AnalysisResponse,
  type CompanyEconomicsResponse,
  type GradingCompaniesResponse,
  type RecommendationResponse,
  type ResultsResponse,
} from "@/lib/api";

// `ApiError` stays real: the screen tells a lost analysis from an outage by its
// `status` and spec §66 `code`, and a fake would not exercise that.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  readAnalysis: vi.fn(),
  readResults: vi.fn(),
  getGradingCompanies: vi.fn(),
}));

const { readAnalysis, readResults, getGradingCompanies } = await import("@/lib/api");
const readAnalysisMock = vi.mocked(readAnalysis);
const readResultsMock = vi.mocked(readResults);
const getGradingCompaniesMock = vi.mocked(getGradingCompanies);

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";
const CARD_ID = "22222222-2222-2222-2222-222222222222";

type ImageQuality = AnalysisResponse["images"][number];

function photograph(
  side: ImageQuality["side"],
  overrides: Partial<ImageQuality> = {},
): ImageQuality {
  return { side, quality_status: "good", quality_score: 0.9, findings: [], ...overrides };
}

/** A front photograph the gate refused, for the reason it gives. */
function unusableFront(): ImageQuality {
  return photograph("front", {
    quality_status: "unusable",
    quality_score: 0.1,
    findings: [{ condition: "blur", verdict: "detected", severity: "unusable" }],
  });
}

function analysis(status: string, overrides: Partial<AnalysisResponse> = {}): AnalysisResponse {
  return {
    id: ANALYSIS_ID,
    status,
    created_at: "2026-09-05T00:00:00Z",
    completed_at: status === "completed" || status === "failed" ? "2026-09-05T00:01:00Z" : null,
    card_id: CARD_ID,
    images: [photograph("front"), photograph("back")],
    // Spec §57's record. Nothing on this screen reads it, but the field is
    // required, so a fixture that omitted it would be a response the service
    // never sends.
    reproducibility: {
      application_version: "0.1.0",
      model_bundle_version: null,
      card_database_version: null,
      grading_rules_version: null,
      market_snapshot_id: null,
      economic_configuration_id: null,
      image_sha256: {},
    },
    ...overrides,
  };
}

function companies(): GradingCompaniesResponse {
  return {
    companies: [
      { company: "psa", display_name: "PSA", grades: ["1", "10"], rules: null },
      { company: "tag", display_name: "TAG", grades: ["1", "10"], rules: null },
      { company: "bgs", display_name: "BGS", grades: ["1", "9.5", "10"], rules: null },
    ],
  };
}

const COSTS: CompanyEconomicsResponse["costs"] = {
  grading_fee: "40.00",
  outbound_shipping: "30.00",
  return_shipping: "30.00",
  insurance: "0.00",
  miscellaneous: "0.00",
  selling_fee: { rate: "0.1000", flat: "0.00" },
};

/** One company with every figure present — the shape a priced analysis answers. */
function company(overrides: Partial<CompanyEconomicsResponse> = {}): CompanyEconomicsResponse {
  return {
    company: "tag",
    grade_distribution: [
      { grade: "9", probability: 0.7 },
      { grade: "10", probability: 0.3 },
    ],
    distribution_confidence: 0.35,
    costs: COSTS,
    expected_graded_value: {
      amount: "234.00",
      confidence: 0.6,
      unpriced_grades: [],
      unpriced_probability: 0,
    },
    expected_graded_value_reason: null,
    incremental_grading_decision: {
      raw_market_value: "100.00",
      raw_selling_fee: "10.00",
      raw_opportunity_value: "90.00",
      graded_proceeds: "214.00",
      grading_costs: "100.00",
      incremental_profit: "24.00",
      confidence: 0.6,
      unpriced_grades: [],
      unpriced_probability: 0,
    },
    incremental_reason: null,
    incremental_roi: {
      value: "0.6250",
      capital_at_risk: "190.00",
      confidence: 0.6,
      label: "Return on grading",
    },
    incremental_roi_reason: null,
    investment_return: {
      acquisition_cost: "50.00",
      graded_proceeds: "214.00",
      grading_costs: "100.00",
      investment_profit: "64.00",
      confidence: 0.6,
      unpriced_grades: [],
      unpriced_probability: 0,
    },
    investment_reason: null,
    investment_roi: {
      value: "0.4266",
      capital_at_risk: "150.00",
      confidence: 0.6,
      label: "Return on investment",
    },
    investment_roi_reason: null,
    ...overrides,
  };
}

/** The same company with nothing priced — what a deployment that never ingested answers. */
function unpricedCompany(
  overrides: Partial<CompanyEconomicsResponse> = {},
): CompanyEconomicsResponse {
  return company({
    company: "psa",
    expected_graded_value: null,
    expected_graded_value_reason: "no_graded_price_available",
    incremental_grading_decision: null,
    incremental_reason: "no_raw_price_available",
    incremental_roi: null,
    incremental_roi_reason: "no_raw_price_available",
    investment_return: null,
    investment_reason: "acquisition_cost_not_supplied",
    investment_roi: null,
    investment_roi_reason: "acquisition_cost_not_supplied",
    ...overrides,
  });
}

/** The V1 answer: every recommendation is this admission (#228). */
function admission(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  const lowConfidence = {
    code: "grade_confidence_below_threshold",
    figure: "distribution_confidence",
    value: "0.35",
    threshold: "0.5",
  };
  return {
    recommended_action: "insufficient_information",
    recommended_company: null,
    reason: lowConfidence,
    confidence: 0.35,
    image_quality: 0.8,
    grade_confidence: 0.35,
    figure_confidence: null,
    failed_gates: [
      lowConfidence,
      {
        code: "unpriced_probability_too_high",
        figure: "unpriced_probability",
        value: "0.4",
        threshold: "0.25",
      },
    ],
    comparison: null,
    comparison_reason: "no_company_can_be_ranked",
    ...overrides,
  };
}

function results(overrides: Partial<ResultsResponse> = {}): ResultsResponse {
  return {
    analysis_id: ANALYSIS_ID,
    status: "completed",
    card_id: CARD_ID,
    currency: "SGD",
    economic_configuration: null,
    market_snapshot: null,
    condition: null,
    companies: [unpricedCompany()],
    refused: {},
    recommendation: admission(),
    ...overrides,
  };
}

beforeEach(() => {
  window.sessionStorage.clear();
  rememberAnalysis(ANALYSIS_ID);
  readAnalysisMock.mockReset();
  readAnalysisMock.mockResolvedValue(analysis("completed"));
  readResultsMock.mockReset();
  readResultsMock.mockResolvedValue(results());
  getGradingCompaniesMock.mockReset();
  getGradingCompaniesMock.mockResolvedValue(companies());
});

afterEach(() => {
  vi.useRealTimers();
});

/**
 * Render and wait for the results to land. The waiting line has no heading;
 * the first `<h1>` is the recommendation's, whatever it says.
 */
async function shown(): Promise<void> {
  render(<Results />);
  await screen.findByRole("heading", { level: 1 });
}

describe("arriving", () => {
  it("says so when this tab has no analysis, rather than reading nothing", async () => {
    window.sessionStorage.clear();

    render(<Results />);

    expect(
      await screen.findByRole("heading", { name: "There are no results to show in this tab." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Photograph a card" })).toHaveAttribute(
      "href",
      "/analyze",
    );
    expect(readAnalysisMock).not.toHaveBeenCalled();
  });
});

describe("spec §49 priority 1 — the recommendation", () => {
  it("renders the V1 admission with its figure, its value and its threshold in words", async () => {
    await shown();

    expect(
      screen.getByRole("heading", { name: "There is not enough information to say." }),
    ).toBeInTheDocument();
    // `null` beside an admission is deliberate (#64), and is never a blank.
    expect(screen.getByText("No company")).toBeInTheDocument();
    expect(
      screen.getAllByText("How far the grading model trusts its own grades").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("35%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("50%").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(
        "The grading model does not trust its own grades enough to build a recommendation on.",
      ).length,
    ).toBeGreaterThan(0);
  });

  it("lists every gate that failed, not only the decisive one", async () => {
    await shown();

    expect(
      screen.getByText("Too much of the likely grade range has no market price."),
    ).toBeInTheDocument();
    expect(screen.getByText("How much of the likely grade range has no price")).toBeInTheDocument();
  });

  it("says, in the comparison's own place, when no company could be compared", async () => {
    await shown();

    const section = screen.getByRole("region", { name: "Company comparison" });
    expect(
      within(section).getByText(/No company could be compared: none had the figure/),
    ).toBeInTheDocument();
    // Once, where the comparison would be — not repeated under the recommendation.
    expect(screen.getAllByText(/No company could be compared/)).toHaveLength(1);
  });

  it("shows the confidence and the photograph quality as percents", async () => {
    await shown();

    expect(screen.getByText("Confidence in this answer")).toBeInTheDocument();
    expect(screen.getByText("Photograph quality")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
  });

  it("names the photograph's faults when the quality gate is what failed", async () => {
    readAnalysisMock.mockResolvedValue(
      analysis("completed", {
        images: [
          photograph("front", {
            quality_status: "poor",
            quality_score: 0.3,
            findings: [{ condition: "glare", verdict: "detected", severity: "poor" }],
          }),
          photograph("back"),
        ],
      }),
    );
    readResultsMock.mockResolvedValue(
      results({
        recommendation: admission({
          reason: {
            code: "image_quality_below_threshold",
            figure: "image_quality",
            value: "0.3",
            threshold: "0.5",
          },
          failed_gates: [
            {
              code: "image_quality_below_threshold",
              figure: "image_quality",
              value: "0.3",
              threshold: "0.5",
            },
          ],
          image_quality: 0.3,
        }),
      }),
    );

    await shown();

    expect(screen.getByText(/front/)).toBeInTheDocument();
    expect(screen.getByText("A reflection is covering part of the card.")).toBeInTheDocument();
  });

  it("renders 'not asked yet' for a null recommendation, never the admission", async () => {
    readResultsMock.mockResolvedValue(results({ recommendation: null, companies: [] }));

    await shown();

    expect(
      screen.getByRole("heading", { name: "Nothing has been asked of this analysis yet." }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "There is not enough information to say." }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("No company")).not.toBeInTheDocument();
  });

  it("names the company, by its display name, on a grade recommendation", async () => {
    readResultsMock.mockResolvedValue(
      results({
        companies: [company()],
        recommendation: admission({
          recommended_action: "grade",
          recommended_company: "tag",
          reason: {
            code: "profit_clears_margin",
            figure: "incremental_profit",
            value: "24.00",
            threshold: "5.00",
          },
          failed_gates: [],
          comparison_reason: null,
        }),
      }),
    );

    await shown();

    expect(screen.getByRole("heading", { name: "Grade this card." })).toBeInTheDocument();
    expect(screen.getAllByText("TAG").length).toBeGreaterThan(0);
    expect(screen.queryByText("tag")).not.toBeInTheDocument();
    expect(screen.getByText("SGD 24.00")).toBeInTheDocument();
    expect(screen.getByText("SGD 5.00")).toBeInTheDocument();
  });

  it("does not hide the comparison below an admission", async () => {
    await shown();

    expect(
      screen.getByRole("heading", { name: "What grading is expected to come to" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "PSA" })).toBeInTheDocument();
  });
});

describe("spec §49 priority 2 — the expected economic outcome", () => {
  it("names the two §41 figures apart, under two different headings", async () => {
    readResultsMock.mockResolvedValue(results({ companies: [company()] }));

    await shown();

    expect(
      screen.getByRole("heading", { name: "Is it worth grading this card?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Did buying this card make money?" }),
    ).toBeInTheDocument();
    expect(screen.getByText("+SGD 24.00")).toBeInTheDocument();
    expect(screen.getByText("+SGD 64.00")).toBeInTheDocument();
    // The ratios under the labels the server supplies (ADR 0007), as percents.
    expect(screen.getByText("Return on grading")).toBeInTheDocument();
    expect(screen.getByText("62.5%")).toBeInTheDocument();
    expect(screen.getByText("Return on investment")).toBeInTheDocument();
    expect(screen.getByText("42.66%")).toBeInTheDocument();
    expect(screen.getByText("SGD 234.00")).toBeInTheDocument();
  });

  it("renders a negative profit with its sign, because it is an answer", async () => {
    const decision = company().incremental_grading_decision!;
    readResultsMock.mockResolvedValue(
      results({
        companies: [
          company({ incremental_grading_decision: { ...decision, incremental_profit: "-3.00" } }),
        ],
        recommendation: admission({
          recommended_action: "do_not_grade",
          recommended_company: "tag",
          reason: {
            code: "profit_below_margin",
            figure: "incremental_profit",
            value: "-3.00",
            threshold: "5.00",
          },
          failed_gates: [],
          comparison_reason: null,
        }),
      }),
    );

    await shown();

    expect(screen.getByRole("heading", { name: "Do not grade this card." })).toBeInTheDocument();
    expect(screen.getByText("-SGD 3.00")).toBeInTheDocument();
  });

  it("renders a present-and-null figure as its reason, never as a number", async () => {
    await shown();

    expect(
      screen.getAllByText("No price is recorded for this card ungraded.").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText("You did not say what you paid, so this cannot be answered.").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("No price is recorded for this card graded.")).toBeInTheDocument();
    expect(screen.queryByText(/SGD 0\.00/)).not.toBeInTheDocument();
  });

  it("names an unknown reason code rather than rendering nothing", async () => {
    readResultsMock.mockResolvedValue(
      results({ companies: [unpricedCompany({ incremental_reason: "a_new_reason" })] }),
    );

    await shown();

    expect(screen.getByText(/a_new_reason/)).toBeInTheDocument();
  });

  it("shows how much of the distribution had no price, only when some did not", async () => {
    const decision = company().incremental_grading_decision!;
    readResultsMock.mockResolvedValue(
      results({
        companies: [
          company({
            incremental_grading_decision: {
              ...decision,
              unpriced_grades: ["10"],
              unpriced_probability: 0.3,
            },
          }),
        ],
      }),
    );

    await shown();

    expect(screen.getByText(/30% of the likely grades had no price/)).toBeInTheDocument();
  });

  it("never presents a total", async () => {
    readResultsMock.mockResolvedValue(results({ companies: [company()] }));

    await shown();

    expect(screen.queryByText(/total/i)).not.toBeInTheDocument();
  });
});

describe("spec §49 priority 3 — the grade distribution", () => {
  it("charts every company's full distribution under the economic outcome, with its confidence beside it", async () => {
    readResultsMock.mockResolvedValue(
      results({
        companies: [
          unpricedCompany(),
          company({
            company: "bgs",
            grade_distribution: [
              { grade: "9", probability: 0.5 },
              { grade: "9.5", probability: 0.3 },
              { grade: "10", probability: 0.2 },
            ],
            distribution_confidence: 0.35,
          }),
        ],
      }),
    );

    await shown();

    const section = screen.getByRole("region", { name: "Grade probabilities" });
    expect(
      within(section).getByRole("figure", { name: "PSA grade probabilities" }),
    ).toBeInTheDocument();
    const bgs = within(section).getByRole("figure", { name: "BGS grade probabilities" });
    expect(
      within(bgs)
        .getAllByRole("rowheader")
        .map((cell) => cell.textContent),
    ).toEqual(["9", "9.5", "10"]);
    // The one number beside each chart, in the words the copy table already has.
    expect(
      within(section).getAllByText("How far the grading model trusts its own grades"),
    ).toHaveLength(2);
    expect(within(section).getAllByText("35%")).toHaveLength(2);
    // The chart replaced its placeholder; the condition's is still held.
    expect(screen.queryByText(/arrive with #247/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Condition" })).toBeInTheDocument();
  });

  it("keeps the recommendation and the economic outcome above the distribution", async () => {
    await shown();

    const headings = screen.getAllByRole("heading").map((heading) => heading.textContent);
    const economics = headings.indexOf("What grading is expected to come to");
    const grades = headings.indexOf("Grade probabilities");
    expect(economics).toBeGreaterThan(0);
    expect(grades).toBeGreaterThan(economics);
  });

  it("holds no chart section when no company has predicted", async () => {
    readResultsMock.mockResolvedValue(results({ companies: [], recommendation: null }));

    await shown();

    expect(screen.queryByRole("region", { name: "Grade probabilities" })).not.toBeInTheDocument();
  });
});

describe("spec §49's second screen — the company comparison", () => {
  it("ranks the companies in the engine's order under the chosen mode, with the unranked apart", async () => {
    readResultsMock.mockResolvedValue(
      results({
        companies: [company(), company({ company: "psa" })],
        refused: { bgs: "condition_step_not_run" },
        recommendation: admission({
          recommended_action: "grade",
          recommended_company: "tag",
          reason: {
            code: "profit_clears_margin",
            figure: "incremental_profit",
            value: "24.00",
            threshold: "5.00",
          },
          failed_gates: [],
          comparison: {
            mode: "expected_profit",
            label: "Most profit",
            ranked: [
              { company: "tag", value: "24.00", confidence: 0.6, figure: "incremental_profit" },
              { company: "psa", value: "18.50", confidence: 0.6, figure: "incremental_profit" },
            ],
            unranked: [{ company: "bgs", reason: "condition_step_not_run" }],
            tied_at_the_top: [],
          },
          comparison_reason: null,
        }),
      }),
    );

    await shown();

    const section = screen.getByRole("region", { name: "Company comparison" });
    expect(screen.queryByText(/arrives with #248/)).not.toBeInTheDocument();
    const order = within(section).getByRole("list", { name: "Most profit" });
    expect(
      within(order)
        .getAllByRole("listitem")
        .map((item) => within(item).getByRole("heading").textContent),
    ).toEqual(["TAG", "PSA"]);
    const apart = within(section).getByRole("list", { name: "Could not be compared" });
    expect(within(apart).getByRole("heading", { name: "BGS" })).toBeInTheDocument();
    // Between the grade distribution and the condition, in §49's order.
    const headings = screen.getAllByRole("heading").map((heading) => heading.textContent);
    expect(headings.indexOf("Company comparison")).toBeGreaterThan(
      headings.indexOf("Grade probabilities"),
    );
    expect(headings.indexOf("Condition")).toBeGreaterThan(headings.indexOf("Company comparison"));
  });

  it("names the companies whose model refused when nothing could be ranked", async () => {
    readResultsMock.mockResolvedValue(results({ refused: { bgs: "condition_step_not_run" } }));

    await shown();

    const section = screen.getByRole("region", { name: "Company comparison" });
    expect(within(section).getByRole("heading", { name: "BGS" })).toBeInTheDocument();
    expect(within(section).queryByText(/nothing was priced/)).not.toBeInTheDocument();
  });

  it("says nothing was priced when nothing could be ranked and no model refused", async () => {
    await shown();

    const section = screen.getByRole("region", { name: "Company comparison" });
    expect(within(section).getByText(/nothing was priced/)).toBeInTheDocument();
  });

  it("holds no comparison when nothing has been asked", async () => {
    readResultsMock.mockResolvedValue(results({ companies: [], recommendation: null }));

    await shown();

    expect(screen.queryByRole("region", { name: "Company comparison" })).not.toBeInTheDocument();
  });
});

describe("the market snapshot", () => {
  it("date-stamps the figures with the snapshot they were priced against", async () => {
    readResultsMock.mockResolvedValue(
      results({
        companies: [company()],
        market_snapshot: {
          id: "66666666-6666-6666-6666-666666666666",
          generated_at: "2026-08-25T03:00:00Z",
          data_version: "2026-08-25",
        },
      }),
    );

    await shown();

    expect(screen.getByText(/2026-08-25/)).toBeInTheDocument();
    expect(document.querySelector("time")).toHaveAttribute("dateTime", "2026-08-25T03:00:00Z");
  });

  it("says when there is no market data rather than showing an undated figure", async () => {
    await shown();

    expect(screen.getByText(/No market data was recorded for this analysis/)).toBeInTheDocument();
    expect(document.querySelector("time")).toBeNull();
  });
});

describe("refused companies", () => {
  it("lists each company whose model declined, with its reason, beside the ones that answered", async () => {
    readResultsMock.mockResolvedValue(results({ refused: { bgs: "condition_step_not_run" } }));

    await shown();

    const economics = screen.getByRole("region", { name: "What grading is expected to come to" });
    expect(within(economics).getByRole("heading", { name: "BGS" })).toBeInTheDocument();
    expect(
      within(economics).getByText(
        "The card's condition was never assessed, so no grade could be predicted.",
      ),
    ).toBeInTheDocument();
    expect(within(economics).getByRole("heading", { name: "PSA" })).toBeInTheDocument();
  });

  it("falls back to the slug when the company list cannot be read", async () => {
    getGradingCompaniesMock.mockRejectedValue(new ApiError("down"));

    await shown();

    expect(screen.getByRole("heading", { name: "psa" })).toBeInTheDocument();
  });
});

describe("waiting on the analysis", () => {
  it("polls until the analysis is finished, then reads the results once", async () => {
    vi.useFakeTimers();
    readAnalysisMock
      .mockResolvedValueOnce(analysis("analyzing"))
      .mockResolvedValueOnce(analysis("completed"));

    render(<Results />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for the costs to be set.");
    expect(readResultsMock).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(readAnalysisMock).toHaveBeenCalledTimes(2);
    expect(readResultsMock).toHaveBeenCalledTimes(1);
  });

  it("stops polling when the screen is left", async () => {
    vi.useFakeTimers();
    readAnalysisMock.mockResolvedValue(analysis("analyzing"));

    const { unmount } = render(<Results />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const [, signal] = readAnalysisMock.mock.calls[0] as [string, AbortSignal];
    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(signal.aborted).toBe(true);
    expect(readAnalysisMock).toHaveBeenCalledTimes(1);
    expect(readResultsMock).not.toHaveBeenCalled();
  });

  it("explains a failed analysis in the gate's own words when a photograph was refused", async () => {
    readAnalysisMock.mockResolvedValue(
      analysis("failed", { images: [unusableFront(), photograph("back")] }),
    );

    render(<Results />);

    expect(
      await screen.findByRole("heading", { name: "This analysis could not be completed." }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/The front photograph could not support an analysis/),
    ).toBeInTheDocument();
    expect(screen.getByText("It is out of focus.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Photograph the card again" })).toHaveAttribute(
      "href",
      "/analyze",
    );
    expect(readResultsMock).not.toHaveBeenCalled();
  });

  it("does not blame the photographs for a failure that was not theirs", async () => {
    readAnalysisMock.mockResolvedValue(analysis("failed"));

    render(<Results />);

    await screen.findByRole("heading", { name: "This analysis could not be completed." });
    expect(
      screen.getByText(/nothing suggests the photographs were the problem/),
    ).toBeInTheDocument();
  });
});

describe("when something goes wrong", () => {
  it("sends a lost analysis back to the start", async () => {
    readAnalysisMock.mockRejectedValue(new ApiError("gone", { status: 404 }));

    render(<Results />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer available/);
    expect(screen.getByRole("link", { name: "Photograph a card" })).toHaveAttribute(
      "href",
      "/analyze",
    );
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("offers a retry when a store would not answer, and reads again on it", async () => {
    readResultsMock
      .mockRejectedValueOnce(new ApiError("down", { status: 503, code: "provider_error" }))
      .mockResolvedValueOnce(results());

    render(<Results />);

    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", { name: "There is not enough information to say." }),
    ).toBeInTheDocument();
    expect(readResultsMock).toHaveBeenCalledTimes(2);
  });
});
