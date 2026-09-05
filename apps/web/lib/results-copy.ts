/**
 * The words for what the results carry as codes.
 *
 * `GET /analyses/{id}/results` speaks in machine names on purpose: spec §50
 * forbids an explanation unrelated to the evidence, so #64 made `reason`
 * `code`/`figure`/`value`/`threshold` with no sentence, and #65 put every
 * company's missing figure beside a bare reason string. Turning those into
 * English is this app's job, and this module is where all of it lives — one
 * sentence per code, keyed off the code the way `./quality-copy` keys off
 * `QualityCondition`.
 *
 * **Every lookup has a fallback that names the code.** The codes are plain
 * strings on the wire, so a new one arrives without a type error; a `Record`
 * over a union would not catch it and an empty string would render a
 * recommendation with no reason shown. Naming the code is honest and is a
 * sentence a person can report.
 *
 * Nothing here converts money to a `number` (#66): an amount is shown as the
 * string it arrived as, prefixed with the currency.
 */

import { proportionToPercent } from "./amount-input";

/**
 * Spec §44's three actions, as a headline. `insufficient_information` is an
 * answer, not an absence — the copy states it as one rather than softening it.
 */
const ACTION_COPY: Readonly<Record<string, string>> = {
  grade: "Grade this card.",
  do_not_grade: "Do not grade this card.",
  insufficient_information: "There is not enough information to say.",
};

export function actionHeadline(action: string): string {
  return (
    ACTION_COPY[action] ?? `The recommendation is "${action}", which this page has no words for.`
  );
}

/**
 * Every reason code the results can carry, from three writers: the engine's
 * gates (#64), the engine's figure admissions (#59–#62, on the wire as each
 * company's `*_reason`), and the worker's refusals (#187, #227, in `refused`).
 * One table, because the codes are disjoint and a screen reads them the same
 * way — beside the thing they explain.
 */
const REASON_COPY: Readonly<Record<string, string>> = {
  // §44's gates, in the order the engine checks them.
  no_company_can_be_ranked:
    "No company could be compared: none had the figure the chosen way of ranking needs.",
  image_quality_below_threshold:
    "The photographs were not good enough to build a recommendation on.",
  grade_confidence_below_threshold:
    "The grading model does not trust its own grades enough to build a recommendation on.",
  incremental_figure_unavailable: "Whether grading pays could not be worked out.",
  unpriced_probability_too_high: "Too much of the likely grade range has no market price.",
  figure_confidence_below_threshold:
    "The expected value once graded is too weakly trusted to decide on.",
  profit_clears_margin:
    "Grading is expected to make enough more than selling as it is to be worth it.",
  profit_below_margin:
    "Grading is not expected to make enough more than selling as it is to be worth it.",
  // The figures' own admissions.
  no_raw_price_available: "No price is recorded for this card ungraded.",
  no_graded_price_available: "No price is recorded for this card graded.",
  acquisition_cost_not_supplied: "You did not say what you paid, so this cannot be answered.",
  no_capital_at_risk: "Nothing was put at risk, so there is nothing to have returned on.",
  // The worker's refusals.
  condition_step_not_run:
    "The card's condition was never assessed, so no grade could be predicted.",
  no_normalized_artifact_for_front:
    "No card could be located in the front photograph, so its condition could not be read.",
  no_normalized_artifact_for_back:
    "No card could be located in the back photograph, so its condition could not be read.",
  no_card_frame_for_front:
    "The front photograph could not be measured, so the card's condition could not be read.",
  no_card_frame_for_back:
    "The back photograph could not be measured, so the card's condition could not be read.",
};

export function reasonCopy(code: string): string {
  return REASON_COPY[code] ?? `The service gave a reason this page has no words for: "${code}".`;
}

/**
 * How a figure's value is shown. Money keeps the wire's string; a proportion
 * becomes a percent; a count is a count.
 */
type FigureKind = "money" | "proportion" | "count";

/**
 * The one vocabulary `ReasonResponse.figure` and `RankedCompanyResponse.figure`
 * share (#63, #64), so #248's comparison table reads the same labels. Nothing
 * here is labelled `roi`: §43's `roi` is a mode name and no figure carries it.
 */
const FIGURES: Readonly<Record<string, { readonly label: string; readonly kind: FigureKind }>> = {
  incremental_profit: {
    label: "Extra money from grading rather than selling as it is",
    kind: "money",
  },
  incremental_roi: { label: "Return on the money grading puts at risk", kind: "proportion" },
  grading_costs: { label: "What grading costs", kind: "money" },
  graded_proceeds: { label: "What the graded card should fetch", kind: "money" },
  unpriced_probability: {
    label: "How much of the likely grade range has no price",
    kind: "proportion",
  },
  distribution_confidence: {
    label: "How far the grading model trusts its own grades",
    kind: "proportion",
  },
  image_quality: { label: "Photograph quality", kind: "proportion" },
  graded_expectation_confidence: {
    label: "How far the value once graded can be trusted",
    kind: "proportion",
  },
  ranked_companies: { label: "Companies that could be compared", kind: "count" },
};

/** `P(10)` and `P(9_or_higher)` are grade probabilities, spelled by the engine. */
const PROBABILITY = /^P\((.+)\)$/;

export function figureLabel(figure: string): string {
  const probability = PROBABILITY.exec(figure);
  if (probability?.[1] !== undefined) {
    return `Chance of a ${probability[1].replaceAll("_", " ")}`;
  }
  return FIGURES[figure]?.label ?? figure;
}

/**
 * The value as a person reads it, or `null` when there was none — a propagated
 * admission is the absence of a figure (#64), and no number is invented for it.
 */
export function formatFigure(
  figure: string,
  value: string | null,
  currency: string,
): string | null {
  if (value === null) return null;
  const kind: FigureKind = PROBABILITY.test(figure)
    ? "proportion"
    : (FIGURES[figure]?.kind ?? "count");
  switch (kind) {
    case "money":
      return `${currency} ${value}`;
    case "proportion":
      return `${proportionToPercent(value)}%`;
    case "count":
      return value;
  }
}

/**
 * A profit with its sign shown either way: a negative figure is an answer, not
 * an error (#60), and a `+` makes the positive case as deliberate as the minus.
 */
export function signedAmount(amount: string, currency: string): string {
  return amount.startsWith("-") ? `-${currency} ${amount.slice(1)}` : `+${currency} ${amount}`;
}

/** A confidence in `[0, 1]` as a whole percent. Not money, so arithmetic is fine here. */
export function percentOf(value: number): string {
  return `${Math.round(value * 100)}%`;
}
