/**
 * The words for spec §6's condition block, said to somebody holding the card.
 *
 * `GET /analyses/{id}/results` carries the condition as the domain's own
 * vocabulary — spec §14's corner labels, §15's edge labels, §16's surface
 * classes, #158's severity ordinal, the four corners and four edges as object
 * keys — and no prose (#245). Turning those into English is this app's job,
 * and this module is where the label copy lives, keyed the way `./quality-copy`
 * keys off `QualityCondition`. The *reasons* an axis was refused for are
 * `./results-copy`'s, beside every other reason on the screen.
 *
 * The label and severity tables are `Record`s over the generated unions rather
 * than lookups with a fallback, so that a thirteenth surface class is a type
 * error here: a label that shipped mute would be damage the model found that
 * nobody could read. Regions and classes arrive as untyped object keys, so
 * those two lookups keep a fallback that names the key.
 */

import type { components } from "./api-types";

type CornerLabel = components["schemas"]["CornerLabel"];
type EdgeLabel = components["schemas"]["EdgeLabel"];
type SurfaceLabel = components["schemas"]["SurfaceLabel"];
type DefectSeverity = components["schemas"]["DefectSeverity"];

/**
 * One name per label, across all three vocabularies. `whitening` and `dent`
 * sit in more than one list and read the same in each; `clean` and `unknown`
 * are the two labels that name no defect (#180), and are said as what they
 * are — found sound, and looked at but not judged — never as a defect.
 */
const LABEL_COPY: Readonly<Record<CornerLabel | EdgeLabel | SurfaceLabel, string>> = {
  clean: "Clean",
  whitening: "Whitening",
  rounding: "Rounding",
  chipping: "Chipping",
  dent: "Dent",
  crease: "Crease",
  layering: "Layering",
  rough_cut: "Rough cut",
  notching: "Notching",
  scratch: "Scratch",
  print_line: "Print line",
  indentation: "Indentation",
  stain: "Stain",
  scuff: "Scuff",
  print_dot: "Print dot",
  color_issue: "Colour issue",
  registration_issue: "Print registration issue",
  gloss_issue: "Gloss issue",
  factory_defect: "Factory defect",
  unknown: "Could not be judged",
};

/** The same classes in the plural, for the list of what was not looked for. */
const CLASS_COPY: Readonly<Record<SurfaceLabel, string>> = {
  scratch: "Scratches",
  print_line: "Print lines",
  dent: "Dents",
  indentation: "Indentations",
  stain: "Stains",
  scuff: "Scuffs",
  print_dot: "Print dots",
  color_issue: "Colour issues",
  registration_issue: "Print registration issues",
  gloss_issue: "Gloss issues",
  factory_defect: "Factory defects",
  unknown: "Anything unnameable",
};

const SEVERITY_COPY: Readonly<Record<DefectSeverity, string>> = {
  minor: "minor",
  moderate: "moderate",
  severe: "severe",
};

/** Spec §14's corners in reading order and §15's edges clockwise, as the wire keys them. */
const REGION_COPY: Readonly<Record<string, string>> = {
  top_left: "Top left",
  top_right: "Top right",
  bottom_left: "Bottom left",
  bottom_right: "Bottom right",
  top: "Top",
  right: "Right",
  bottom: "Bottom",
  left: "Left",
};

/**
 * A finding as one phrase: the label, and its severity when it has one.
 * `clean` and `unknown` carry none (#180), so they read as the label alone.
 */
export function findingCopy(finding: {
  readonly label: CornerLabel | EdgeLabel | SurfaceLabel;
  readonly severity: DefectSeverity | null;
}): string {
  const label = LABEL_COPY[finding.label];
  return finding.severity === null ? label : `${label}, ${SEVERITY_COPY[finding.severity]}`;
}

export function severityCopy(severity: DefectSeverity): string {
  return SEVERITY_COPY[severity];
}

export function regionCopy(region: string): string {
  return REGION_COPY[region] ?? region.replaceAll("_", " ");
}

/** A surface class in the plural; a class this page has no words for is named. */
export function classCopy(cls: string): string {
  return (CLASS_COPY as Readonly<Record<string, string>>)[cls] ?? cls.replaceAll("_", " ");
}

/**
 * A centering ratio as spec §50 prints one — `57/43` — from the wire's
 * `left / (left + right)` (or `top / (top + bottom)`), where 0.5 is perfect.
 * Whole percents, and the pair always sums to 100 so a reader can check it.
 */
export function ratioLabel(ratio: number): string {
  const near = Math.round(ratio * 100);
  return `${String(near)}/${String(100 - near)}`;
}
