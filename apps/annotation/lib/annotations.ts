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
import type { Representation } from "./api";
import type { Fraction, Size } from "./viewport";

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
 * Where the card sits inside its artifact, as fractions of the artifact.
 *
 * The service derives this from the artifact's own stored normalization record
 * (#194 put a margin of photograph around the card), so it is right for
 * whatever version produced the artifact. `WHOLE_ARTIFACT` is the pre-margin
 * frame — an artifact whose card really does reach the edges.
 */
export interface CardFrame {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export const WHOLE_ARTIFACT: CardFrame = { x: 0, y: 0, width: 1, height: 1 };

/** Four corners, clockwise from the top left — the shape of a clicked box. */
export type Quad = readonly [Fraction, Fraction, Fraction, Fraction];

/**
 * Four clicked points as a deterministic clockwise cycle from the top left.
 *
 * The annotator clicks corners in whatever order their hand takes; the cycle is
 * fixed by the angle around the centroid (y grows downward, so ascending angle
 * is clockwise on screen) and the phase by the corner nearest the origin —
 * `ml/card-detection` orders a detected quadrilateral the same way, for the
 * same reason: side names mean nothing until the corners have an order.
 */
export function orderedQuad(points: readonly Fraction[]): Quad {
  const centre = {
    x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
    y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
  };
  const cycle = [...points].sort(
    (a, b) =>
      Math.atan2(a.y - centre.y, a.x - centre.x) - Math.atan2(b.y - centre.y, b.x - centre.x),
  );
  let start = 0;
  for (let index = 1; index < cycle.length; index += 1) {
    const candidate = cycle[index];
    const best = cycle[start];
    if (candidate && best && candidate.x + candidate.y < best.x + best.y) start = index;
  }
  const rotated = [...cycle.slice(start), ...cycle.slice(0, start)];
  return [rotated[0], rotated[1], rotated[2], rotated[3]] as unknown as Quad;
}

/** Midpoint of one side of a quad, sides indexed clockwise from the top. */
function sideMidpoint(quad: Quad, side: number, image: Size): { x: number; y: number } {
  const a = quad[side % 4] as Fraction;
  const b = quad[(side + 1) % 4] as Fraction;
  return { x: ((a.x + b.x) / 2) * image.width, y: ((a.y + b.y) / 2) * image.height };
}

/** Unsigned distance from a point to the line through one side of a quad, in pixels. */
function sideDistance(
  point: { x: number; y: number },
  quad: Quad,
  side: number,
  image: Size,
): number {
  const a = quad[side % 4] as Fraction;
  const b = quad[(side + 1) % 4] as Fraction;
  const ax = a.x * image.width;
  const ay = a.y * image.height;
  const bx = b.x * image.width;
  const by = b.y * image.height;
  const length = Math.hypot(bx - ax, by - ay);
  if (length === 0) return 0;
  return Math.abs((bx - ax) * (point.y - ay) - (by - ay) * (point.x - ax)) / length;
}

/**
 * Spec §21's two ratios, from two clicked quadrilaterals.
 *
 * The four-point form of {@link centeringFrom}, for the card that does not sit
 * straight in the frame: each border is the distance from the midpoint of an
 * inner side to the line through the matching outer side, measured in artifact
 * **pixels** — fractions are of two different lengths, and a ratio mixing them
 * would skew whichever axis the artifact is longer in. Rotating card and frame
 * together changes nothing, which is the point.
 *
 * Sides are matched by the shared clockwise-from-top-left order
 * ({@link orderedQuad}): top to top, right to right. The null rules are
 * {@link centeringFrom}'s own.
 *
 * ponytail: unsigned distances — an inner corner clicked *outside* the outer
 * edge still measures a positive border. The quads are drawn seconds apart on
 * the same card, so the case is a mis-click the annotator can see and redo.
 */
export function centeringFromQuads(
  inner: Quad,
  outer: Quad,
  axes: MeasuredAxes,
  image: Size,
): { horizontal: number | null; vertical: number | null } | null {
  if (!axes.horizontal && !axes.vertical) return null;

  const border = (side: number) =>
    sideDistance(sideMidpoint(inner, side, image), outer, side, image);
  const top = border(0);
  const right = border(1);
  const bottom = border(2);
  const left = border(3);
  if (axes.horizontal && left + right <= 0) return null;
  if (axes.vertical && top + bottom <= 0) return null;

  return {
    horizontal: axes.horizontal ? left / (left + right) : null,
    vertical: axes.vertical ? top / (top + bottom) : null,
  };
}

/**
 * Spec §21's two ratios, from the gap between two drawn boxes.
 *
 * **The borders are measured against the card's own rectangle**, and since the
 * artifact keeps a margin of photograph around the card (#194) that rectangle
 * is the annotator's first box — the card's outer edge, traced where the card
 * meets the background. Not the detector's `card_frame`: a border is a few
 * percent of the card, so a few pixels of quad error swings the ratio wildly.
 * So the left border is `box.x - card.x`, the right is
 * `card.x + card.width - (box.x + box.width)`:
 *
 *     horizontal = left / (left + right)
 *
 * A box edge that strays into the margin clamps its border to zero — the card
 * has no border outside itself.
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
  card: CardFrame = WHOLE_ARTIFACT,
): { horizontal: number | null; vertical: number | null } | null {
  if (!axes.horizontal && !axes.vertical) return null;

  const left = Math.max(0, box.x - card.x);
  const right = Math.max(0, card.x + card.width - (box.x + box.width));
  const top = Math.max(0, box.y - card.y);
  const bottom = Math.max(0, card.y + card.height - (box.y + box.height));
  if (axes.horizontal && left + right <= 0) return null;
  if (axes.vertical && top + bottom <= 0) return null;

  return {
    horizontal: axes.horizontal ? left / (left + right) : null,
    vertical: axes.vertical ? top / (top + bottom) : null,
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
 * Which frame a staged marker's coordinates are fractions of.
 *
 * A corner or edge is always in the artifact's frame — ADR 0010 measured both
 * adequate against it, and #175 changes the coordinate space of surface
 * annotations only, which is why only the surface request carries the field.
 */
export function markerRepresentation(marker: MarkerRequest): Representation {
  return marker.kind === "surface" ? marker.representation : "normalized";
}

/**
 * Whether anything staged is a claim about the standardized artifact.
 *
 * The mirror of the service's gate, exactly: a centering ratio, a corner or
 * edge box, and a surface marker declaring `normalized` (box or not) all need
 * the artifact; a surface marker declaring `original` never does, because the
 * photograph always exists (#175). Asked here so the annotator finds out while
 * they can still change it rather than from a 409.
 */
export function requiresArtifact(
  markers: readonly MarkerDraft[],
  centering: CenteringRequest | null,
): boolean {
  return (
    centering !== null ||
    markers.some((draft) =>
      draft.marker.kind === "surface"
        ? draft.marker.representation === "normalized"
        : draft.marker.bbox != null,
    )
  );
}

/** Prose for a vocabulary member. The slugs are the service's; the words are ours. */
export function readable(slug: string): string {
  return slug.replace(/_/g, " ");
}
