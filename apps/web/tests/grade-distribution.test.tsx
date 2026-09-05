import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GradeDistribution, gradeLabel, percentLabel } from "@/app/results/GradeDistribution";
import type { GradeProbabilityResponse } from "@/lib/api";

/**
 * The chart is drawn from the wire and assumes nothing about the ladder: the
 * distribution arrives §63-valid and ascending (#228), and every term is a bar
 * whatever its key says. These fixtures are the V1 shape — mass over the whole
 * ladder, most of it under 1 % — with the keys `GET /grading-companies` spells.
 */

/** PSA's eighteen points as the predictor spreads them: one 9.5-less ladder. */
const PSA_LADDER = [
  "1",
  "1.5",
  "2",
  "2.5",
  "3",
  "3.5",
  "4",
  "4.5",
  "5",
  "5.5",
  "6",
  "6.5",
  "7",
  "7.5",
  "8",
  "8.5",
  "9",
  "10",
] as const;

function psa(): GradeProbabilityResponse[] {
  // Sixteen tail points at 0.004 (6.4 %), then 0.216, 0.42, 0.3 — a legal sum.
  return PSA_LADDER.map((grade, index) => ({
    grade,
    probability: index < 15 ? 0.004 : ([0.216, 0.42, 0.3][index - 15] ?? 0),
  }));
}

/** Spec §6's own example, with a collapsed tail. */
function specExample(): GradeProbabilityResponse[] {
  return [
    { grade: "7_or_lower", probability: 0.02 },
    { grade: "8", probability: 0.17 },
    { grade: "9", probability: 0.69 },
    { grade: "10", probability: 0.12 },
  ];
}

// Vitest runs with `apps/web` as its root.
function source(path: string): string {
  return readFileSync(join(process.cwd(), path), "utf8");
}

function rows() {
  return screen.getAllByRole("row").filter((row) => within(row).queryByRole("rowheader"));
}

describe("the ladder is the wire's", () => {
  it("draws every grade on the wire as a labelled bar, in wire order", () => {
    render(<GradeDistribution name="PSA" distribution={psa()} />);

    const headers = screen.getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(headers).toEqual([...PSA_LADDER]);
    expect(rows()).toHaveLength(18);
  });

  it("draws a 9.5 bar for a ladder that has one and never for one that does not", () => {
    const bgs = [...psa()];
    bgs.splice(17, 0, { grade: "9.5", probability: 0.1 });
    const { unmount } = render(<GradeDistribution name="BGS" distribution={bgs} />);
    expect(screen.getByRole("rowheader", { name: "9.5" })).toBeInTheDocument();
    unmount();

    render(<GradeDistribution name="PSA" distribution={psa()} />);
    expect(screen.queryByRole("rowheader", { name: "9.5" })).not.toBeInTheDocument();
  });

  it("renders a collapsed tail as its inequality and leaves every other key alone", () => {
    expect(gradeLabel("7_or_lower")).toBe("≤ 7");
    expect(gradeLabel("9_or_higher")).toBe("≥ 9");
    expect(gradeLabel("9.5")).toBe("9.5");
    expect(gradeLabel("10")).toBe("10");

    render(<GradeDistribution name="PSA" distribution={specExample()} />);
    expect(screen.getByRole("rowheader", { name: "≤ 7" })).toBeInTheDocument();
  });
});

describe("the bars say what the table says", () => {
  it("gives each row's bar the row's probability as its width and its percent as its label", () => {
    render(<GradeDistribution name="PSA" distribution={specExample()} />);

    const widths = rows().map(
      (row) => (row.querySelector("[data-bar]") as HTMLElement).style.inlineSize,
    );
    expect(widths).toEqual(["2%", "17%", "69%", "12%"]);

    const cells = rows().map((row) => within(row).getByRole("cell").textContent);
    expect(cells).toEqual(["2%", "17%", "69%", "12%"]);
  });

  it("keeps a bar under one percent on screen and labels it honestly", () => {
    expect(percentLabel(0.004)).toBe("<1%");
    expect(percentLabel(0)).toBe("0%");
    expect(percentLabel(0.005)).toBe("1%");
    expect(percentLabel(0.42)).toBe("42%");

    render(<GradeDistribution name="PSA" distribution={psa()} />);

    const first = rows()[0] as HTMLElement;
    expect(within(first).getByRole("rowheader")).toHaveTextContent("1");
    expect(within(first).getByRole("cell")).toHaveTextContent("<1%");
    expect((first.querySelector("[data-bar]") as HTMLElement).style.inlineSize).toBe("0.4%");
  });

  it("names the chart after the company, in spec §49's words", () => {
    render(<GradeDistribution name="TAG" distribution={specExample()} />);

    expect(screen.getByRole("figure", { name: "TAG grade probabilities" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "TAG grade probabilities" })).toBeInTheDocument();
  });
});

describe("colour comes from the tokens", () => {
  // A literal here would be a palette that drifts from `tokens.css`, and one
  // that does not follow the colour scheme. `dataviz` validated the token's two
  // values against this app's own surfaces; the component only names the role.
  // `#247` is an issue number, not a colour; a hex colour is never three digits
  // on its own in this repository.
  const LITERAL = /#(?!\d{3}\b)[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(/i;

  it("carries no colour literal in the component or its styles", () => {
    expect(source("app/results/GradeDistribution.tsx")).not.toMatch(LITERAL);
    const chart = /\/\* -+ grade distribution \*\/([\s\S]*?)(?=\/\* -+ \w|$)/.exec(
      source("app/results/page.module.css"),
    );
    expect(chart?.[1]).toBeDefined();
    expect(chart?.[1]).not.toMatch(LITERAL);
    expect(chart?.[1]).toMatch(/var\(--color-series-1\)/);
  });

  it("defines the series colour on :root and again for the dark scheme", () => {
    const tokens = source("styles/tokens.css");
    const dark = tokens.indexOf("@media (prefers-color-scheme: dark)");
    expect(dark).toBeGreaterThan(0);
    expect(tokens.slice(0, dark)).toMatch(/--color-series-1\s*:\s*#[0-9a-f]{6};/i);
    expect(tokens.slice(dark)).toMatch(/--color-series-1\s*:\s*#[0-9a-f]{6};/i);
  });

  it("draws horizontal bars, so a long ladder stacks down a phone rather than across it", () => {
    render(<GradeDistribution name="PSA" distribution={psa()} />);

    // Width from the probability, never height: the ladder is the rows.
    for (const row of rows()) {
      const bar = row.querySelector("[data-bar]") as HTMLElement;
      expect(bar.style.inlineSize).toMatch(/%$/);
      expect(bar.style.blockSize).toBe("");
    }
  });
});
