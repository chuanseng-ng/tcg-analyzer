import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConditionAssessment } from "@/app/results/ConditionAssessment";
import type {
  ConditionRefusalResponse,
  ConditionResponse,
  RegionFindingResponse,
  SurfaceResponse,
} from "@/lib/api";
import { ratioLabel } from "@/lib/condition-copy";

/**
 * The block is rendered from `ResultsResponse.condition` and decides nothing:
 * labels, severities and regions are the wire's enums, every refusal is the
 * one-key object wherever it sits (#245), and the words are the copy tables'.
 * These fixtures are the V1 shape — whitening-only corners and edges, a
 * stain-and-scuff-only surface with nine classes refused per side, the
 * manufacturing derivation refused, eye appeal refused — plus the two shapes
 * V1 cannot produce but the wire allows: a real surface finding, and a
 * manufacturing defect list.
 */

const FINE = "below the sampling limit of the 12 px/mm artifact (ADR 0010)";
const DEPTH = "a depth signal one normalized view does not carry";
const REFERENCE = "no reference image to compare against (ADR 0004)";
const TEMPLATE = "no print template to measure registration against";
const JUDGEMENT = "a manufacturing judgement this baseline cannot make";
const BORDERLESS = "the frame touches the card edge on this axis, so there is no border to ratio";

function refusal(reason: string | null): ConditionRefusalResponse {
  return { insufficient_information: reason };
}

function clean(): RegionFindingResponse {
  return { label: "clean", severity: null, confidence: 0.8 };
}

function surface(overrides: Partial<SurfaceResponse> = {}): SurfaceResponse {
  return {
    findings: [],
    not_assessed: {
      scratch: refusal(FINE),
      print_line: refusal(FINE),
      print_dot: refusal(FINE),
      gloss_issue: refusal(FINE),
      dent: refusal(DEPTH),
      indentation: refusal(DEPTH),
      color_issue: refusal(REFERENCE),
      registration_issue: refusal(TEMPLATE),
      factory_defect: refusal(JUDGEMENT),
    },
    ...overrides,
  };
}

/** A V1 assessment of a card with one whitened corner and nothing else found. */
function assessed(overrides: Partial<ConditionResponse> = {}): ConditionResponse {
  return {
    version: "condition-compose-v0.1.0",
    confidence: 0.84,
    centering: {
      front_horizontal: 0.57,
      front_vertical: 0.5,
      back_horizontal: 0.48,
      back_vertical: 0.52,
      confidence: 0.9,
    },
    corners: {
      front: {
        top_left: { label: "whitening", severity: "minor", confidence: 0.7 },
        top_right: clean(),
        bottom_left: clean(),
        bottom_right: { label: "unknown", severity: null, confidence: 0.3 },
      },
      back: { top_left: clean(), top_right: clean(), bottom_left: clean(), bottom_right: clean() },
    },
    edges: {
      front: { top: clean(), right: clean(), bottom: clean(), left: clean() },
      back: {
        top: clean(),
        right: { label: "whitening", severity: "moderate", confidence: 0.6 },
        bottom: clean(),
        left: clean(),
      },
    },
    surface: { front: surface(), back: surface() },
    manufacturing_defects: refusal("manufacturing_classes_not_assessed"),
    eye_appeal: refusal("eye_appeal_not_measured_in_v1"),
    ...overrides,
  };
}

/** The step ran and declined outright: every axis wears the one reason. */
function declined(reason = "no_card_frame_for_back"): ConditionResponse {
  return {
    version: "condition-compose-v0.1.0",
    confidence: null,
    centering: refusal(reason),
    corners: { front: refusal(reason), back: refusal(reason) },
    edges: { front: refusal(reason), back: refusal(reason) },
    surface: { front: refusal(reason), back: refusal(reason) },
    manufacturing_defects: refusal(reason),
    eye_appeal: refusal(reason),
  };
}

function shown(condition: ConditionResponse | null) {
  return render(<ConditionAssessment condition={condition} />);
}

function side(name: "Front" | "Back"): HTMLElement {
  return screen.getByRole("region", { name });
}

function fact(scope: HTMLElement, term: string): string | null {
  const dt = within(scope).getByText(term);
  return dt.nextElementSibling?.textContent ?? null;
}

describe("the block", () => {
  it("renders the front and then the back, each with every axis", () => {
    shown(assessed());

    const headings = screen.getAllByRole("heading").map((heading) => heading.textContent);
    expect(headings.indexOf("Front")).toBeGreaterThan(-1);
    expect(headings.indexOf("Back")).toBeGreaterThan(headings.indexOf("Front"));
    for (const name of ["Front", "Back"] as const) {
      const card = side(name);
      for (const axis of ["Centering", "Corners", "Edges", "Surface"]) {
        expect(within(card).getByRole("heading", { name: axis })).toBeInTheDocument();
      }
    }
  });

  it("shows nothing that is a coordinate, an overlay or a score", () => {
    shown(assessed());

    expect(document.body.textContent).not.toMatch(/\b(x|y|width|height|score)\b/i);
    expect(document.querySelector("img, svg, canvas")).toBeNull();
  });
});

describe("centering", () => {
  it("renders each measured ratio as spec §50 prints it, per side", () => {
    shown(assessed());

    expect(fact(side("Front"), "Left to right")).toBe("57/43");
    expect(fact(side("Front"), "Top to bottom")).toBe("50/50");
    expect(fact(side("Back"), "Left to right")).toBe("48/52");
  });

  it("renders a refused ratio as its reason, beside the ones that were measured", () => {
    shown(
      assessed({
        centering: {
          front_horizontal: 0.55,
          front_vertical: refusal(BORDERLESS),
          back_horizontal: 0.5,
          back_vertical: 0.5,
          confidence: 0.7,
        },
      }),
    );

    expect(fact(side("Front"), "Left to right")).toBe("55/45");
    expect(fact(side("Front"), "Top to bottom")).toMatch(/no ratio to measure/);
    expect(document.body.textContent).not.toContain(BORDERLESS);
  });

  it("renders a whole-axis refusal on both sides", () => {
    shown(assessed({ centering: refusal("the artifact could not be decoded") }));

    expect(fact(side("Front"), "Centering")).toMatch(/could not be decoded/);
    expect(fact(side("Back"), "Centering")).toMatch(/could not be decoded/);
  });
});

describe("corners and edges", () => {
  it("renders each region with its label and severity in words", () => {
    shown(assessed());

    expect(fact(side("Front"), "Top left")).toBe("Whitening, minor");
    expect(fact(side("Back"), "Right")).toBe("Whitening, moderate");
  });

  it("says a clean region is clean and an unknown one could not be judged", () => {
    shown(assessed());

    expect(fact(side("Front"), "Top right")).toBe("Clean");
    expect(fact(side("Front"), "Bottom right")).toBe("Could not be judged");
  });

  it("renders a refused side as its reason", () => {
    shown(
      assessed({
        corners: {
          front: refusal("the card frame names a region too small to classify"),
          back: assessed().corners["back"] ?? refusal(null),
        },
      }),
    );

    expect(fact(side("Front"), "Corners")).toMatch(/too small/);
    expect(fact(side("Back"), "Top left")).toBe("Clean");
  });
});

describe("surface", () => {
  it("lists every class the analyzer refused, with its reason, even when nothing was found", () => {
    shown(assessed());

    const front = within(side("Front")).getByRole("region", { name: "Surface" });
    expect(within(front).queryByText(/clean/i)).not.toBeInTheDocument();
    expect(within(front).getByText(/Nothing was found among/)).toBeInTheDocument();
    for (const cls of [
      /scratches/i,
      /print lines/i,
      /print dots/i,
      /gloss issues/i,
      /dents/i,
      /indentations/i,
      /colour issues/i,
      /print registration issues/i,
      /factory defects/i,
    ]) {
      expect(within(front).getByText(cls)).toBeInTheDocument();
    }
    expect(within(front).getByText(/Too fine for the photograph/)).toBeInTheDocument();
    expect(within(front).getByText(/Depth cannot be read/)).toBeInTheDocument();
  });

  it("counts the findings per class and severity rather than listing each blob", () => {
    // The V1 analyzer reports every stain it segments as its own finding, so a
    // real front carries dozens; a count per class is what a person can read.
    const stain = (severity: "minor" | "moderate" | "severe") =>
      ({ type: "stain", severity, confidence: 0.6, side: "front" }) as const;
    shown(
      assessed({
        surface: {
          front: surface({
            findings: [
              stain("minor"),
              stain("severe"),
              stain("minor"),
              { type: "scuff", severity: "moderate", confidence: 0.5, side: "front" },
            ],
          }),
          back: surface(),
        },
      }),
    );

    const front = within(side("Front")).getByRole("region", { name: "Surface" });
    expect(within(front).getByText("Stains: 2 minor, 1 severe")).toBeInTheDocument();
    expect(within(front).getByText("Scuffs: 1 moderate")).toBeInTheDocument();
    expect(within(front).queryByText(/Nothing was found/)).not.toBeInTheDocument();
  });

  it("renders a refused side as its reason", () => {
    shown(assessed({ surface: { front: refusal(null), back: surface() } }));

    expect(fact(side("Front"), "Surface")).toMatch(/no reason/i);
  });
});

describe("manufacturing defects and eye appeal", () => {
  it("renders the V1 refusals in words", () => {
    shown(assessed());

    expect(screen.getByText(/were not looked for/)).toBeInTheDocument();
    expect(screen.getByText(/Eye appeal is not measured yet/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("manufacturing_classes_not_assessed");
    expect(document.body.textContent).not.toContain("eye_appeal_not_measured_in_v1");
  });

  it("renders a defect list with each defect's side", () => {
    shown(
      assessed({
        manufacturing_defects: [
          { type: "rough_cut", severity: "minor", confidence: 0.5, side: "back" },
        ],
      }),
    );

    expect(screen.getByText("Back: rough cut, minor")).toBeInTheDocument();
  });

  it("says when nothing was found rather than nothing at all", () => {
    shown(assessed({ manufacturing_defects: [] }));

    const block = screen.getByRole("region", { name: "Manufacturing defects" });
    expect(within(block).getByText(/None found/)).toBeInTheDocument();
  });
});

describe("condition confidence", () => {
  it("is a percent", () => {
    shown(assessed());

    expect(screen.getByText("Condition confidence").nextElementSibling?.textContent).toBe("84%");
  });

  it("is never 0% when nothing was measured", () => {
    shown(declined());

    expect(screen.getByText("Condition confidence").nextElementSibling?.textContent).toBe(
      "Not measured",
    );
    expect(document.body.textContent).not.toMatch(/\b0%/);
  });
});

describe("the two kinds of nothing", () => {
  it("says the step has not run when the condition is null, and nothing else", () => {
    shown(null);

    expect(screen.getByText(/not been assessed yet/)).toBeInTheDocument();
    expect(screen.queryByText(/Condition confidence/)).not.toBeInTheDocument();
    expect(screen.queryByText(/declined|could not/)).not.toBeInTheDocument();
  });

  it("renders a step that declined as the reason on every axis, not as 'not yet'", () => {
    shown(declined());

    expect(screen.queryByText(/not been assessed yet/)).not.toBeInTheDocument();
    const reason = "The back photograph could not be measured";
    expect(within(side("Front")).getAllByText(new RegExp(reason)).length).toBeGreaterThan(0);
    expect(within(side("Back")).getAllByText(new RegExp(reason)).length).toBeGreaterThan(0);
  });

  it("names a reason it has no words for rather than rendering nothing", () => {
    shown(declined("a_new_reason"));

    expect(screen.getAllByText(/a_new_reason/).length).toBeGreaterThan(0);
  });
});

describe("ratioLabel", () => {
  it("prints the larger-border side first as a whole-percent pair summing to 100", () => {
    expect(ratioLabel(0.57)).toBe("57/43");
    expect(ratioLabel(0.5)).toBe("50/50");
    expect(ratioLabel(0.426)).toBe("43/57");
  });
});
