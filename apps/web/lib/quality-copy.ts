/**
 * What spec §19's quality gate found, said to somebody holding a phone.
 *
 * The API returns a condition, a verdict and a severity, and no prose: the
 * measurement behind a finding is a Laplacian variance or a saturated-pixel
 * fraction, which is not something to put in front of a user and is not
 * something the API should be inventing sentences about either. All the copy is
 * here, and there is exactly one sentence per condition.
 *
 * **Every sentence names the photograph, not the gate.** "Too dark to read" is
 * something a person can act on; "excessive darkness detected" is a log line.
 * None of them is an instruction — telling somebody *how* to retake the
 * photograph is guided photography, which spec §52 puts after V1.
 *
 * `poor` and `unusable` share their wording. §19 makes the difference a
 * consequence, not a description: the same fault either stops the analysis or
 * merely gets mentioned, and saying "quite blurred" versus "very blurred" would
 * be inventing a distinction the gate did not measure.
 */

import type { components } from "./api-types";

type QualityCondition = components["schemas"]["QualityCondition"];
type ImageQuality = components["schemas"]["ImageQualityResponse"];
type UploadSide = components["schemas"]["ImageSide"];

/**
 * One sentence per condition, keyed by the API's own vocabulary.
 *
 * Written as a `Record` over the generated union rather than a lookup with a
 * fallback, so that a twelfth condition is a type error here — a condition that
 * shipped mute would be a photograph refused for a reason nobody could read.
 */
const CONDITION_COPY: Readonly<Record<QualityCondition, string>> = {
  blur: "It is out of focus.",
  low_resolution: "It is too small for the detail this needs.",
  glare: "A reflection is covering part of the card.",
  poor_exposure: "The light is too flat to make anything out.",
  excessive_darkness: "It is too dark to read.",
  excessive_brightness: "It is washed out by the light.",
  severe_perspective_distortion: "The card is at too much of an angle.",
  card_partly_outside_frame: "Part of the card is outside the picture.",
  multiple_cards: "There seems to be more than one card in the picture.",
  sleeve_obstruction: "A sleeve is covering the card.",
  insufficient_card_size: "The card is too small in the frame.",
};

/** How each side is named in a sentence about it. */
const SIDE_COPY: Readonly<Record<UploadSide, string>> = {
  front: "front",
  back: "back",
  angled_front: "angled front",
  angled_back: "angled back",
  surface_front: "front surface",
  surface_back: "back surface",
};

export function nameOf(side: UploadSide): string {
  return SIDE_COPY[side] ?? side;
}

/**
 * What was actually wrong with one photograph, in the order the gate reported.
 *
 * Only `detected` findings. A condition the gate could not check is not a
 * complaint — the screen has nothing to offer about it, and listing "could not
 * check for sleeves" beside a real fault would bury the real fault.
 */
export function faultsIn(image: ImageQuality): readonly string[] {
  return image.findings
    .filter((finding) => finding.verdict === "detected")
    .map((finding) => CONDITION_COPY[finding.condition]);
}

/** Whether this photograph is one the gate refused outright. */
export function isUnusable(image: ImageQuality): boolean {
  return image.quality_status === "unusable";
}

/** Whether this photograph went through with a warning — §19's `poor`. */
export function isPoor(image: ImageQuality): boolean {
  return image.quality_status === "poor";
}

/**
 * The photographs worth saying something about, worst first.
 *
 * `good` and `acceptable` are silent by design: §19 gives them no consequence,
 * and a screen that reported every verdict would train people to ignore the one
 * that matters.
 */
export function concerning(images: readonly ImageQuality[]): readonly ImageQuality[] {
  // `filter` already produced a new array, so sorting it in place mutates
  // nothing the caller can see.
  return images
    .filter((image) => isUnusable(image) || isPoor(image))
    .sort((left, right) => Number(isUnusable(right)) - Number(isUnusable(left)));
}
