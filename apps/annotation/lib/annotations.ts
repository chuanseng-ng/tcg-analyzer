/**
 * What an annotator is building, before any of it is written.
 *
 * Kept out of the components because all of it is arithmetic and vocabulary, and
 * both are things a test should be able to assert without rendering anything.
 * `lib/viewport.ts` owns the map from the screen to the artifact; this owns what
 * is done with the fractions it produces.
 *
 * **Drafts exist because the schema is append-only.** `image_annotations` and
 * `centering_measurements` both refuse an `UPDATE`, so a marker written in error
 * cannot be corrected — only added to. A mistake therefore has to be removable
 * *before* it is saved, which is why nothing here posts as it is placed.
 */

import type { components } from "./api-types";
import type { Fraction } from "./viewport";

export type CornerRegion = components["schemas"]["CornerRegion"];
export type EdgeRegion = components["schemas"]["EdgeRegion"];
export type CornerLabel = components["schemas"]["CornerLabel"];
export type EdgeLabel = components["schemas"]["EdgeLabel"];
export type SurfaceLabel = components["schemas"]["SurfaceLabel"];
export type DefectSeverity = components["schemas"]["DefectSeverity"];
export type BoundingBox = components["schemas"]["BoundingBoxModel"];
export type MarkerRequest =
  | components["schemas"]["CornerMarkerRequest"]
  | components["schemas"]["EdgeMarkerRequest"]
  | components["schemas"]["SurfaceMarkerRequest"];
export type CenteringRequest = components["schemas"]["CenteringReadingRequest"];
export type AnnotationRequestBody = components["schemas"]["AnnotationRequest"];

/**
 * The vocabularies, at runtime.
 *
 * TypeScript's unions are erased, and a `<select>` needs values — so these lists
 * exist, and the two lines under each are what stops them drifting from the
 * schema they came from. `readonly CornerLabel[]` refuses a member the service
 * does not know; `Exclude<…> extends never` refuses one the service knows and
 * this forgot. **Both directions, or the drift is one-way and silent.**
 *
 * Spec §14, §15 and §16 in the specification's own order, and they are **three
 * lists rather than one**: an edge has `rough_cut` and `notching`, which a corner
 * cannot have, and a corner has `rounding` and `crease`, which an edge cannot.
 */
export const CORNER_REGIONS = ["top_left", "top_right", "bottom_left", "bottom_right"] as const;
export const EDGE_REGIONS = ["top", "right", "bottom", "left"] as const;
export const CORNER_LABELS = [
  "clean",
  "whitening",
  "rounding",
  "chipping",
  "dent",
  "crease",
  "layering",
  "unknown",
] as const;
export const EDGE_LABELS = [
  "clean",
  "whitening",
  "chipping",
  "rough_cut",
  "notching",
  "layering",
  "dent",
  "unknown",
] as const;
/** Twelve, and **no `clean`**: a surface with nothing wrong is one nobody annotated. */
export const SURFACE_LABELS = [
  "scratch",
  "print_line",
  "dent",
  "indentation",
  "stain",
  "scuff",
  "print_dot",
  "color_issue",
  "registration_issue",
  "gloss_issue",
  "factory_defect",
  "unknown",
] as const;
export const SEVERITIES = ["minor", "moderate", "severe"] as const;

type Covers<Union extends string, Listed extends string> = [Exclude<Union, Listed>] extends [never]
  ? true
  : never;

// Compile-time only. If the service gains a label and this file does not, one of
// these stops being assignable and `pnpm typecheck` fails — which is the whole
// point of writing them out.
const _coversCornerRegions: Covers<CornerRegion, (typeof CORNER_REGIONS)[number]> = true;
const _coversEdgeRegions: Covers<EdgeRegion, (typeof EDGE_REGIONS)[number]> = true;
const _coversCornerLabels: Covers<CornerLabel, (typeof CORNER_LABELS)[number]> = true;
const _coversEdgeLabels: Covers<EdgeLabel, (typeof EDGE_LABELS)[number]> = true;
const _coversSurfaceLabels: Covers<SurfaceLabel, (typeof SURFACE_LABELS)[number]> = true;
const _coversSeverities: Covers<DefectSeverity, (typeof SEVERITIES)[number]> = true;
void [
  _coversCornerRegions,
  _coversEdgeRegions,
  _coversCornerLabels,
  _coversEdgeLabels,
  _coversSurfaceLabels,
  _coversSeverities,
];

/**
 * The two labels that assert *no* defect, and therefore carry no severity.
 *
 * `ck_image_annotations_a_defect_carries_a_severity` is an equality rather than
 * an implication, so this is both directions: `clean` with a severity is refused
 * as firmly as `chipping` without one.
 */
const NO_DEFECT_LABELS: ReadonlySet<string> = new Set(["clean", "unknown"]);

export function requiresSeverity(label: string): boolean {
  return !NO_DEFECT_LABELS.has(label);
}

/**
 * How sure the annotator is, as three steps rather than a slider.
 *
 * The same argument that made severity an ordinal: there is one annotator and no
 * agreement study, so a number they could not reproduce next week is a precision
 * a model would fit as if it meant something. Three values are reproducible.
 *
 * **Nothing is selected by default.** `image_annotations.confidence` is NOT NULL
 * with no server default on purpose — a default would read as certainty for every
 * row nobody thought about — and a pre-checked radio would put that default back
 * where the schema can no longer see it.
 */
export const CONFIDENCE_LEVELS = [
  { value: 0.9, label: "Sure" },
  { value: 0.6, label: "Fairly sure" },
  { value: 0.3, label: "Not sure" },
] as const;

/** What "I cannot tell" records beside the `unknown` label. */
export const CANNOT_TELL_CONFIDENCE = 0.3;

/**
 * A box from two dragged corners, or `null` if it has no area.
 *
 * Normalized, so dragging up-and-left is the same gesture as down-and-right.
 * `null` rather than a zero-extent box because `bbox_width > 0` is a CHECK: a
 * click that did not move is not a region, and a marker made from one would be
 * refused by the service after the annotator thought they had placed it.
 *
 * Both corners are already clamped into the unit square by `fractionAt`, so
 * taking the extent between them cannot escape it — which is what makes
 * `bbox_x + bbox_width <= 1` true by construction rather than by a second check.
 */
export function boxFrom(a: Fraction, b: Fraction): BoundingBox | null {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  const width = Math.abs(a.x - b.x);
  const height = Math.abs(a.y - b.y);

  if (width <= 0 || height <= 0) return null;
  return { x, y, width, height };
}

/** Which axes of a centering measurement the card actually has a border on. */
export interface MeasuredAxes {
  readonly horizontal: boolean;
  readonly vertical: boolean;
}

/**
 * Spec §21's two ratios, from where the annotator put the inner frame.
 *
 * **The artifact's edges are the card's edges** — `ml/normalization` warps the
 * detected quadrilateral onto the whole target with no inset, precisely so that
 * corner and edge analysis sees the real edge — so the left border is the box's
 * `x`, the right border is `1 - (x + width)`, and their sum is `1 - width`:
 *
 *     horizontal = left / (left + right) = x / (1 - width)
 *
 * `0.5` is perfect centering, which is the direction
 * `centering_measurements.horizontal` is documented in. The annotator marks
 * borders and never types a ratio — §21 asks for a measurement, and somebody
 * doing division under time pressure is not one.
 *
 * Returns `null` where the reading would say nothing: a frame filling an axis
 * completely leaves no border to divide by, and `0 / 0` would reach the wire as
 * `null` and be read as *this axis has no measurable border*, which is a
 * different claim entirely. An axis genuinely without a border is `false` in
 * `axes`, and that is the honest way to say it (§21's full-art and borderless
 * layouts).
 */
export function centeringFrom(
  box: BoundingBox,
  axes: MeasuredAxes,
): { horizontal: number | null; vertical: number | null } | null {
  if (!axes.horizontal && !axes.vertical) return null;

  const horizontalBorders = 1 - box.width;
  const verticalBorders = 1 - box.height;
  if (axes.horizontal && horizontalBorders <= 0) return null;
  if (axes.vertical && verticalBorders <= 0) return null;

  return {
    horizontal: axes.horizontal ? box.x / horizontalBorders : null,
    vertical: axes.vertical ? box.y / verticalBorders : null,
  };
}

/** One staged marker. The `id` is the browser's, so a draft can be removed again. */
export interface MarkerDraft {
  readonly id: string;
  readonly marker: MarkerRequest;
}

/** What one Save sends. Never called with nothing — the service refuses that. */
export function requestBodyFrom(
  markers: readonly MarkerDraft[],
  centering: CenteringRequest | null,
): AnnotationRequestBody {
  return {
    markers: markers.map((draft) => draft.marker),
    centering,
  };
}

/** Whether there is anything to save. */
export function hasWork(markers: readonly MarkerDraft[], centering: CenteringRequest | null) {
  return markers.length > 0 || centering !== null;
}

/**
 * Whether anything staged is a fraction of the artifact.
 *
 * The service refuses coordinates for an image no card was located in, so the
 * tool asks the same question before offering to save one — the annotator finds
 * out while they can still change it rather than from a 409.
 */
export function carriesCoordinates(
  markers: readonly MarkerDraft[],
  centering: CenteringRequest | null,
): boolean {
  return centering !== null || markers.some((draft) => draft.marker.bbox != null);
}

/** Prose for a vocabulary member. The slugs are the service's; the words are ours. */
export function readable(slug: string): string {
  return slug.replace(/_/g, " ");
}
