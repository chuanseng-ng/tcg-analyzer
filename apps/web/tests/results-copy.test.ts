import { describe, expect, it } from "vitest";

import {
  actionHeadline,
  figureLabel,
  formatFigure,
  percentOf,
  reasonCopy,
  signedAmount,
} from "@/lib/results-copy";

/** Every code the wire can carry today, from the engine, the worker and the route. */
const KNOWN_CODES = [
  "image_quality_below_threshold",
  "grade_confidence_below_threshold",
  "figure_confidence_below_threshold",
  "unpriced_probability_too_high",
  "incremental_figure_unavailable",
  "profit_clears_margin",
  "profit_below_margin",
  "no_raw_price_available",
  "no_graded_price_available",
  "acquisition_cost_not_supplied",
  "no_capital_at_risk",
  "no_company_can_be_ranked",
  "condition_step_not_run",
  "no_normalized_artifact_for_front",
  "no_normalized_artifact_for_back",
  "no_card_frame_for_front",
  "no_card_frame_for_back",
];

describe("reasonCopy", () => {
  it.each(KNOWN_CODES)("has a sentence for %s", (code) => {
    const sentence = reasonCopy(code);

    expect(sentence).toMatch(/\S/);
    // The copy is written for a person, so the machine name stays out of it.
    expect(sentence).not.toContain(code);
  });

  it("names a code it has no words for rather than rendering nothing", () => {
    // The codes are bare strings on the wire, so a new one arrives silently;
    // an empty string here would be a recommendation with no reason shown.
    expect(reasonCopy("a_new_gate")).toContain("a_new_gate");
  });
});

describe("actionHeadline", () => {
  it("has a headline for each of spec §44's three actions", () => {
    expect(actionHeadline("grade")).toMatch(/grade/i);
    expect(actionHeadline("do_not_grade")).toMatch(/not/i);
    expect(actionHeadline("insufficient_information")).toMatch(/not enough|cannot/i);
  });

  it("names an unknown action rather than inventing a verdict", () => {
    expect(actionHeadline("maybe")).toContain("maybe");
  });
});

describe("figureLabel", () => {
  it("labels every figure the engine ranks or gates on", () => {
    for (const figure of [
      "incremental_profit",
      "incremental_roi",
      "grading_costs",
      "graded_proceeds",
      "unpriced_probability",
      "distribution_confidence",
      "image_quality",
      "graded_expectation_confidence",
      "ranked_companies",
    ]) {
      expect(figureLabel(figure)).not.toBe(figure);
    }
  });

  it("reads a grade probability off its name", () => {
    expect(figureLabel("P(10)")).toBe("Chance of a 10");
    expect(figureLabel("P(9_or_higher)")).toBe("Chance of a 9 or higher");
  });

  it("never labels anything roi", () => {
    // §43's `roi` is a mode name; no figure carries it (#62, #63).
    expect(figureLabel("incremental_roi")).not.toMatch(/\broi\b/i);
    expect(figureLabel("roi")).toBe("roi");
  });
});

describe("formatFigure", () => {
  it("prefixes money with the currency and never reformats the amount", () => {
    expect(formatFigure("incremental_profit", "24.00", "SGD")).toBe("SGD 24.00");
    expect(formatFigure("grading_costs", "100.00", "SGD")).toBe("SGD 100.00");
  });

  it("shows a proportion as a percent", () => {
    expect(formatFigure("distribution_confidence", "0.35", "SGD")).toBe("35%");
    expect(formatFigure("image_quality", "0.5", "SGD")).toBe("50%");
    expect(formatFigure("incremental_roi", "0.6250", "SGD")).toBe("62.5%");
    expect(formatFigure("P(10)", "0.08", "SGD")).toBe("8%");
  });

  it("shows a count as it is", () => {
    expect(formatFigure("ranked_companies", "0", "SGD")).toBe("0");
  });

  it("has nothing to show for an absent value", () => {
    // A propagated admission carries no number (#64); the copy says so, the
    // formatter does not invent one.
    expect(formatFigure("incremental_profit", null, "SGD")).toBeNull();
  });
});

describe("signedAmount", () => {
  it("shows the sign on a profit either way, because negative is an answer", () => {
    expect(signedAmount("24.00", "SGD")).toBe("+SGD 24.00");
    expect(signedAmount("-3.00", "SGD")).toBe("-SGD 3.00");
  });
});

describe("percentOf", () => {
  it("rounds a confidence to a whole percent", () => {
    expect(percentOf(0.35)).toBe("35%");
    expect(percentOf(0.846)).toBe("85%");
  });
});
