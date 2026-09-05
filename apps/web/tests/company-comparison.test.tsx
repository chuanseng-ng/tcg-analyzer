import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompanyComparison } from "@/app/results/CompanyComparison";
import type { CompanyComparisonResponse, RankedCompanyResponse } from "@/lib/api";

/**
 * The comparison is rendered from the wire and decides nothing: the order is
 * the engine's (#63), the reasons are the engine's and the models' (#238), and
 * the words are the copy table's. These fixtures are a priced ladder — the
 * branch V1 never reaches, because nothing is priced — and the V1 admission.
 */

const NAMES: Readonly<Record<string, string>> = { psa: "PSA", tag: "TAG", bgs: "BGS" };
const displayName = (slug: string) => NAMES[slug] ?? slug;

function ranked(
  company: string,
  value: string,
  figure = "incremental_profit",
  confidence = 0.6,
): RankedCompanyResponse {
  return { company, value, confidence, figure };
}

function comparison(overrides: Partial<CompanyComparisonResponse> = {}): CompanyComparisonResponse {
  return {
    mode: "expected_profit",
    label: "Most profit",
    ranked: [ranked("tag", "24.00"), ranked("psa", "18.50", "incremental_profit", 0.55)],
    unranked: [{ company: "bgs", reason: "no_graded_price_available" }],
    tied_at_the_top: [],
    ...overrides,
  };
}

function shown(
  overrides: {
    comparison?: CompanyComparisonResponse | null;
    reason?: string | null;
    refused?: readonly (readonly [string, string])[];
  } = {},
) {
  return render(
    <CompanyComparison
      comparison={overrides.comparison === undefined ? comparison() : overrides.comparison}
      reason={overrides.reason ?? null}
      refused={overrides.refused ?? []}
      currency="SGD"
      displayName={displayName}
    />,
  );
}

function names(list: HTMLElement): (string | null)[] {
  return within(list)
    .getAllByRole("listitem")
    .map((item) => within(item).getByRole("heading").textContent);
}

describe("the ranked companies", () => {
  it("are an ordered list in the wire's order, under the mode's own label", () => {
    shown();

    expect(screen.getByRole("heading", { name: "Most profit" })).toBeInTheDocument();
    const order = screen.getByRole("list", { name: "Most profit" });
    expect(order.tagName).toBe("OL");
    expect(names(order)).toEqual(["TAG", "PSA"]);
  });

  it("show each figure under its own label, as money or a percent by figure", () => {
    shown({
      comparison: comparison({
        mode: "highest_grade_probability",
        label: "Best odds of the top grade",
        ranked: [
          ranked("bgs", "0.3", "P(10)", 0.35),
          ranked("psa", "0.25", "P(9_or_higher)", 0.35),
        ],
        unranked: [],
      }),
    });

    const order = screen.getByRole("list", { name: "Best odds of the top grade" });
    const [bgs, psa] = within(order).getAllByRole("listitem") as [HTMLElement, HTMLElement];
    expect(within(bgs).getByText("Chance of a 10")).toBeInTheDocument();
    expect(within(bgs).getByText("30%")).toBeInTheDocument();
    expect(within(psa).getByText("Chance of a 9 or higher")).toBeInTheDocument();
    expect(within(psa).getByText("25%")).toBeInTheDocument();
  });

  it("keep the wire's money string and show the confidence as a percent", () => {
    shown();

    expect(screen.getByText("SGD 24.00")).toBeInTheDocument();
    expect(screen.getByText("SGD 18.50")).toBeInTheDocument();
    expect(screen.getAllByText("How far this figure is trusted")).toHaveLength(2);
    expect(screen.getByText("60%")).toBeInTheDocument();
    expect(screen.getByText("55%")).toBeInTheDocument();
  });

  it("show a return on grading as a percent and never label it roi", () => {
    shown({
      comparison: comparison({
        mode: "roi",
        label: "Best return on grading",
        ranked: [ranked("tag", "0.6250", "incremental_roi")],
        unranked: [{ company: "psa", reason: "no_raw_price_available" }],
      }),
    });

    expect(screen.getByText("62.5%")).toBeInTheDocument();
    expect(screen.getByText("Return on the money grading puts at risk")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\broi\b/i);
  });
});

describe("the unranked companies", () => {
  it("are an unordered list apart from the ranked ones, each with its reason in words", () => {
    shown({
      comparison: comparison({
        ranked: [ranked("tag", "24.00")],
        unranked: [
          { company: "bgs", reason: "no_graded_price_available" },
          { company: "psa", reason: "condition_step_not_run" },
        ],
      }),
    });

    expect(names(screen.getByRole("list", { name: "Most profit" }))).toEqual(["TAG"]);
    const apart = screen.getByRole("list", { name: "Could not be compared" });
    expect(apart.tagName).toBe("UL");
    expect(names(apart)).toEqual(["BGS", "PSA"]);
    expect(
      within(apart).getByText("No price is recorded for this card graded."),
    ).toBeInTheDocument();
    expect(
      within(apart).getByText(
        "The card's condition was never assessed, so no grade could be predicted.",
      ),
    ).toBeInTheDocument();
  });

  it("have no list when every company was ranked", () => {
    shown({ comparison: comparison({ unranked: [] }) });

    expect(screen.queryByRole("list", { name: "Could not be compared" })).not.toBeInTheDocument();
  });
});

describe("a tie at the top", () => {
  it("is said, by display name, when more than one company shares first place", () => {
    shown({
      comparison: comparison({
        ranked: [ranked("psa", "24.00"), ranked("tag", "24.00"), ranked("bgs", "10.00")],
        unranked: [],
        tied_at_the_top: ["psa", "tag"],
      }),
    });

    expect(screen.getByText(/PSA and TAG are tied for first/)).toBeInTheDocument();
    expect(screen.getByText(/means nothing/)).toBeInTheDocument();
  });

  it("is not said for a single leader", () => {
    shown();

    expect(screen.queryByText(/tied/)).not.toBeInTheDocument();
  });
});

describe("when no company could be ranked", () => {
  it("renders the admission and one line per company whose model refused", () => {
    shown({
      comparison: null,
      reason: "no_company_can_be_ranked",
      refused: [["bgs", "condition_step_not_run"]],
    });

    expect(screen.getByText(/No company could be compared/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Most profit" })).not.toBeInTheDocument();
    const declined = screen.getByRole("list", { name: "Could not be compared" });
    expect(names(declined)).toEqual(["BGS"]);
    expect(
      within(declined).getByText(
        "The card's condition was never assessed, so no grade could be predicted.",
      ),
    ).toBeInTheDocument();
  });

  it("says that nothing was priced when no model refused either", () => {
    shown({ comparison: null, reason: "no_company_can_be_ranked", refused: [] });

    expect(screen.getByText(/No company could be compared/)).toBeInTheDocument();
    expect(screen.getByText(/nothing was priced/)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("names a reason it has no words for rather than rendering nothing", () => {
    shown({ comparison: null, reason: "a_new_reason", refused: [] });

    expect(screen.getByText(/a_new_reason/)).toBeInTheDocument();
  });
});
