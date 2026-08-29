import { describe, expect, it } from "vitest";

import {
  boxFrom,
  centeringFrom,
  CONFIDENCE_LEVELS,
  CORNER_LABELS,
  EDGE_LABELS,
  hasWork,
  markerRepresentation,
  requiresArtifact,
  requiresSeverity,
  requestBodyFrom,
  SURFACE_LABELS,
  type MarkerDraft,
} from "@/lib/annotations";

describe("a box from two dragged corners", () => {
  it("normalizes either direction of drag", () => {
    const downRight = boxFrom({ x: 0.2, y: 0.3 }, { x: 0.5, y: 0.8 });
    const upLeft = boxFrom({ x: 0.5, y: 0.8 }, { x: 0.2, y: 0.3 });

    expect(downRight).toEqual({ x: 0.2, y: 0.3, width: 0.3, height: 0.5 });
    expect(upLeft).toEqual(downRight);
  });

  it("refuses a drag with no area rather than making a box the schema would reject", () => {
    // `bbox_width > 0` is a CHECK. A click that did not move is not a region, and
    // a marker made from one would be refused after the annotator believed they
    // had placed it.
    expect(boxFrom({ x: 0.4, y: 0.4 }, { x: 0.4, y: 0.4 })).toBeNull();
    expect(boxFrom({ x: 0.4, y: 0.1 }, { x: 0.4, y: 0.9 })).toBeNull();
  });

  it("cannot leave the unit square, because its corners never do", () => {
    // `fractionAt` clamps each corner, so taking the extent between two clamped
    // points satisfies `bbox_x + bbox_width <= 1` by construction.
    const box = boxFrom({ x: 0, y: 0 }, { x: 1, y: 1 });

    expect(box).not.toBeNull();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(1);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(1);
  });
});

describe("centering, derived rather than typed", () => {
  const both = { horizontal: true, vertical: true };

  it("reads the borders off what is left outside the inner frame", () => {
    // A frame inset 20% on the left and 30% on the right: borders 0.2 and 0.3,
    // so the left is 0.2 / 0.5 = 0.4 of the pair.
    const ratios = centeringFrom({ x: 0.2, y: 0.25, width: 0.5, height: 0.5 }, both);

    expect(ratios?.horizontal).toBeCloseTo(0.4, 10);
    expect(ratios?.vertical).toBeCloseTo(0.5, 10);
  });

  it("calls a perfectly centred frame 0.5, which is the direction the column means", () => {
    const ratios = centeringFrom({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, both);

    expect(ratios).toEqual({ horizontal: 0.5, vertical: 0.5 });
  });

  it("sends null for an axis with no border, and never 0.5", () => {
    // Spec §21's full-art and borderless layouts. Inventing 0.5 for one of them
    // is the confidently-wrong output the invariants forbid.
    const ratios = centeringFrom(
      { x: 0.2, y: 0.25, width: 0.5, height: 0.5 },
      {
        horizontal: true,
        vertical: false,
      },
    );

    expect(ratios?.vertical).toBeNull();
    expect(ratios?.horizontal).toBeCloseTo(0.4, 10);
  });

  it("refuses a box that leaves no border rather than dividing by zero", () => {
    // 0 / 0 is NaN, `JSON.stringify` writes it as null, and null on the wire
    // means *this axis has no measurable border* — a different claim entirely.
    expect(centeringFrom({ x: 0, y: 0.25, width: 1, height: 0.5 }, both)).toBeNull();
    expect(centeringFrom({ x: 0.25, y: 0, width: 0.5, height: 1 }, both)).toBeNull();
  });

  it("does not refuse a full-width box on an axis nobody is measuring", () => {
    expect(
      centeringFrom(
        { x: 0, y: 0.25, width: 1, height: 0.5 },
        { horizontal: false, vertical: true },
      ),
    ).toEqual({ horizontal: null, vertical: 0.5 });
  });

  it("measures the borders against the card's frame, not the artifact's edges", () => {
    // #194: the artifact holds a margin of photograph around the card, so the
    // card's own rectangle arrives from the service. A box centred in the CARD
    // is 0.5/0.5 even though it is not centred in the artifact.
    const frame = { x: 24 / 804, y: 24 / 1104, width: 756 / 804, height: 1056 / 1104 };
    const box = {
      x: frame.x + frame.width * 0.25,
      y: frame.y + frame.height * 0.25,
      width: frame.width * 0.5,
      height: frame.height * 0.5,
    };

    const ratios = centeringFrom(box, both, frame);

    expect(ratios?.horizontal).toBeCloseTo(0.5, 10);
    expect(ratios?.vertical).toBeCloseTo(0.5, 10);
  });

  it("reads an off-centre frame off the card's edge, not the margin's", () => {
    // Card frame insets 0.1 each side; inner box leaves 0.1 of border on the
    // left and 0.3 on the right *of the card* — 0.25 of the pair.
    const frame = { x: 0.1, y: 0.1, width: 0.8, height: 0.8 };
    const ratios = centeringFrom({ x: 0.2, y: 0.2, width: 0.4, height: 0.4 }, both, frame);

    expect(ratios?.horizontal).toBeCloseTo((0.2 - 0.1) / (0.2 - 0.1 + 0.9 - 0.6), 10);
    expect(ratios?.vertical).toBeCloseTo(0.25, 10);
  });

  it("clamps a box that strays into the margin rather than going negative", () => {
    const frame = { x: 0.1, y: 0.1, width: 0.8, height: 0.8 };
    // The box's left edge is inside the margin: the left border is 0, not -0.05.
    const ratios = centeringFrom({ x: 0.05, y: 0.2, width: 0.5, height: 0.4 }, both, frame);

    expect(ratios?.horizontal).toBe(0);
  });

  it("refuses a box that fills the card's frame rather than dividing by zero", () => {
    const frame = { x: 0.1, y: 0.1, width: 0.8, height: 0.8 };
    expect(centeringFrom({ x: 0.1, y: 0.3, width: 0.8, height: 0.4 }, both, frame)).toBeNull();
  });

  it("refuses a reading of neither axis, which the schema refuses too", () => {
    expect(
      centeringFrom(
        { x: 0.25, y: 0.25, width: 0.5, height: 0.5 },
        {
          horizontal: false,
          vertical: false,
        },
      ),
    ).toBeNull();
  });
});

describe("the vocabularies", () => {
  it("keeps §14, §15 and §16 apart", () => {
    // Three lists rather than one. `rough_cut` is a cutting defect an edge has
    // and a corner has not; `crease` is the reverse.
    expect(EDGE_LABELS).toContain("rough_cut");
    expect(CORNER_LABELS).not.toContain("rough_cut");
    expect(CORNER_LABELS).toContain("crease");
    expect(EDGE_LABELS).not.toContain("crease");
  });

  it("carries no `clean` for a surface, because a clean surface is no rows at all", () => {
    expect(SURFACE_LABELS).not.toContain("clean");
    expect(CORNER_LABELS).toContain("clean");
  });

  it("ends every list with `unknown`, which is where uncertainty lives", () => {
    for (const labels of [CORNER_LABELS, EDGE_LABELS, SURFACE_LABELS]) {
      expect(labels.at(-1)).toBe("unknown");
    }
  });
});

describe("severity", () => {
  it("is required for a defect and refused for a label that asserts none", () => {
    // The CHECK is an equality, not an implication: `clean` with a severity is
    // as wrong as `chipping` without one.
    expect(requiresSeverity("chipping")).toBe(true);
    expect(requiresSeverity("clean")).toBe(false);
    expect(requiresSeverity("unknown")).toBe(false);
  });
});

describe("confidence", () => {
  it("is three reproducible steps rather than a slider", () => {
    expect(CONFIDENCE_LEVELS).toHaveLength(3);
    for (const level of CONFIDENCE_LEVELS) {
      expect(level.value).toBeGreaterThan(0);
      expect(level.value).toBeLessThanOrEqual(1);
    }
  });
});

describe("what one save sends", () => {
  const draft = (bbox: MarkerDraft["marker"]["bbox"]): MarkerDraft => ({
    id: "draft-1",
    marker: {
      kind: "corner",
      region: "top_left",
      label: "whitening",
      severity: "minor",
      confidence: 0.8,
      bbox,
    },
  });

  it("is exactly what was staged", () => {
    const staged = [draft({ x: 0.1, y: 0.1, width: 0.1, height: 0.1 })];

    expect(requestBodyFrom(staged, null)).toEqual({
      markers: [staged[0]?.marker],
      centering: null,
    });
  });

  it("knows whether anything is staged at all", () => {
    expect(hasWork([], null)).toBe(false);
    expect(hasWork([draft(null)], null)).toBe(true);
    expect(hasWork([], { horizontal: 0.5, vertical: null, confidence: 0.9, notes: null })).toBe(
      true,
    );
  });

  const surface = (
    representation: "normalized" | "original",
    bbox: MarkerDraft["marker"]["bbox"],
  ): MarkerDraft => ({
    id: "draft-2",
    marker: {
      kind: "surface",
      label: "scratch",
      severity: "minor",
      confidence: 0.6,
      representation,
      bbox,
    },
  });

  const box = { x: 0.1, y: 0.1, width: 0.1, height: 0.1 };

  it("knows whether anything staged is a claim about the artifact", () => {
    // The service answers 409 for exactly this, so the screen asks the same
    // question while the annotator can still change it.
    expect(requiresArtifact([draft(null)], null)).toBe(false);
    expect(requiresArtifact([draft(box)], null)).toBe(true);
    expect(
      requiresArtifact([], { horizontal: 0.5, vertical: null, confidence: 0.9, notes: null }),
    ).toBe(true);
  });

  it("lets surface work declared against the original photograph pass the gate", () => {
    // #175: the photograph always exists, so a surface box against it needs no
    // artifact — and a surface declaring "normalized" makes the claim even
    // without a box, exactly as the service reads it.
    expect(requiresArtifact([surface("original", box)], null)).toBe(false);
    expect(requiresArtifact([surface("normalized", box)], null)).toBe(true);
    expect(requiresArtifact([surface("normalized", null)], null)).toBe(true);
  });

  it("names the frame each staged marker belongs to", () => {
    // The overlay filters rects by this: the two frames relate by a projective
    // warp, so a fraction of one is never drawn over the other.
    expect(markerRepresentation(draft(box).marker)).toBe("normalized");
    expect(markerRepresentation(surface("original", box).marker)).toBe("original");
    expect(markerRepresentation(surface("normalized", box).marker)).toBe("normalized");
  });
});
