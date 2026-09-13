import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api-types";
import { concerning, faultsIn, isPoor, isUnusable, nameOf } from "@/lib/quality-copy";

type ImageQuality = components["schemas"]["ImageQualityResponse"];
type QualityCondition = components["schemas"]["QualityCondition"];
type Finding = components["schemas"]["QualityFindingResponse"];

/**
 * Spec §19's eleven, written out here rather than derived from the generated
 * types, so that a condition the service starts sending and this app has no
 * sentence for is caught by a test rather than by a blank line on the screen.
 */
const CONDITIONS: readonly QualityCondition[] = [
  "blur",
  "low_resolution",
  "glare",
  "poor_exposure",
  "excessive_darkness",
  "excessive_brightness",
  "severe_perspective_distortion",
  "card_partly_outside_frame",
  "multiple_cards",
  "sleeve_obstruction",
  "insufficient_card_size",
];

function detected(condition: QualityCondition, severity: "poor" | "unusable"): Finding {
  return { condition, verdict: "detected", severity };
}

function image(overrides: Partial<ImageQuality> = {}): ImageQuality {
  return {
    side: "front",
    quality_status: "acceptable",
    quality_score: 0.9,
    findings: [],
    refusal: null,
    ...overrides,
  };
}

describe("quality copy", () => {
  it("has a sentence for every condition the service can report", () => {
    for (const condition of CONDITIONS) {
      const faults = faultsIn(image({ findings: [detected(condition, "poor")] }));

      expect(faults, condition).toHaveLength(1);
      expect(faults[0]!.trim(), condition).not.toBe("");
    }
  });

  it("says nothing about a condition that was merely checked and cleared", () => {
    const clear = image({ findings: [{ condition: "blur", verdict: "clear", severity: null }] });

    expect(faultsIn(clear)).toEqual([]);
  });

  it("says nothing about a condition it could not check", () => {
    // Five wait on card detection. Listing "could not check for sleeves" beside
    // a real fault would bury the real fault.
    const unchecked = image({
      findings: [{ condition: "multiple_cards", verdict: "undetermined", severity: null }],
    });

    expect(faultsIn(unchecked)).toEqual([]);
  });

  it("names the missing card when the gate refused the photograph outright", () => {
    // #319. The gate never located a card, so nothing was `detected` and the
    // screen had a refusal with an empty list of faults under it.
    const refused = image({ quality_status: "unusable", refusal: "no_card_found" });

    expect(faultsIn(refused)).toEqual(["No card could be found in the picture."]);
  });

  it("says the card is in a case when that is why it was refused", () => {
    // #320. "No card could be found" would send a slab owner back to retake a
    // photograph that can never pass.
    const refused = image({ quality_status: "unusable", refusal: "card_in_a_case" });

    expect(faultsIn(refused)).toEqual([
      "The card is in a graded case. Photograph it out of the case.",
    ]);
  });

  it("puts the refusal before the faults the gate did manage to find", () => {
    const refused = image({
      quality_status: "unusable",
      refusal: "no_card_found",
      findings: [detected("blur", "poor")],
    });

    expect(faultsIn(refused)[0]).toBe("No card could be found in the picture.");
    expect(faultsIn(refused)).toHaveLength(2);
  });

  it("names each side in words a person would use", () => {
    expect(nameOf("front")).toBe("front");
    expect(nameOf("back")).toBe("back");
  });

  it("mentions only the photographs with a consequence", () => {
    const images = [
      image({ side: "front", quality_status: "good" }),
      image({ side: "back", quality_status: "poor" }),
    ];

    expect(concerning(images).map((one) => one.side)).toEqual(["back"]);
  });

  it("says nothing at all about a photograph the gate has not judged", () => {
    expect(concerning([image({ quality_status: null, quality_score: null })])).toEqual([]);
  });

  it("puts the refused photograph before the merely imperfect one", () => {
    const images = [
      image({ side: "front", quality_status: "poor" }),
      image({ side: "back", quality_status: "unusable" }),
    ];

    expect(concerning(images).map((one) => one.side)).toEqual(["back", "front"]);
  });

  it("distinguishes a refusal from a warning", () => {
    expect(isUnusable(image({ quality_status: "unusable" }))).toBe(true);
    expect(isPoor(image({ quality_status: "unusable" }))).toBe(false);
    expect(isPoor(image({ quality_status: "poor" }))).toBe(true);
  });
});
