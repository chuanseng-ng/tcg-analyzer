import { useId } from "react";

import type {
  CenteringResponse,
  ConditionRefusalResponse,
  ConditionResponse,
  DefectResponse,
  RegionFindingResponse,
  SurfaceResponse,
} from "@/lib/api";
import { classCopy, findingCopy, ratioLabel, regionCopy, severityCopy } from "@/lib/condition-copy";
import { nameOf, SIDES } from "@/lib/quality-copy";
import { percentOf, reasonCopy } from "@/lib/results-copy";

import { Fact } from "./Fact";
import styles from "./page.module.css";

type Refusal = ConditionRefusalResponse;
type Side = (typeof SIDES)[number];
type Regions = Record<string, RegionFindingResponse>;
type Severity = DefectResponse["severity"];

/** #158's ordinal, worst last; `null` is the unrated `unknown` (#180). */
const SEVERITIES: readonly Severity[] = ["minor", "moderate", "severe", null];

/**
 * Spec §6's Condition block — issue #249, §49's fourth priority. Rendered from
 * `ResultsResponse.condition` and nothing else (#245), and it decides nothing:
 * the labels are §14–§16's, the severities #158's, the sides and regions the
 * wire's keys, and every word is a copy table's.
 *
 * **Two kinds of nothing, told apart.** `condition: null` means the step never
 * ran and is said as "not yet". A step that ran and declined is a block whose
 * every axis wears the stored reason and whose confidence is "Not measured" —
 * never `0%` (#91). Within an answer, a refusal is told from a measurement by
 * the presence of `insufficient_information` and by nothing else.
 *
 * **What was not looked for is shown.** The V1 analyzers read corners and edges
 * for whitening and the surface for stains and scuffs (#183–#185); every class
 * the surface analyzer refused travels in `not_assessed` and is listed here
 * with its reason, so an empty finding list is never read as a clean card.
 *
 * Nothing here is a coordinate, an overlay or a score (spec §4, §2.2).
 */
export function ConditionAssessment({
  condition,
}: {
  readonly condition: ConditionResponse | null;
}) {
  if (condition === null) {
    return (
      <p className={styles.body}>
        The card&apos;s condition has not been assessed yet — the step that reads it has not run.
      </p>
    );
  }

  const sides = SIDES.filter(
    (side) => side in condition.corners || side in condition.edges || side in condition.surface,
  );

  return (
    <>
      <dl className={styles.facts}>
        <Fact
          term="Condition confidence"
          value={condition.confidence === null ? "Not measured" : percentOf(condition.confidence)}
          supplied={condition.confidence !== null}
        />
      </dl>
      {sides.map((side) => (
        <SideAssessment key={side} side={side} condition={condition} />
      ))}
      <Manufacturing defects={condition.manufacturing_defects} />
      <dl className={styles.facts}>
        <Fact term="Eye appeal" value={refusalCopy(condition.eye_appeal)} supplied={false} />
      </dl>
    </>
  );
}

function isRefusal(value: unknown): value is Refusal {
  return typeof value === "object" && value !== null && "insufficient_information" in value;
}

function refusalCopy(refusal: Refusal): string {
  return reasonCopy(refusal.insufficient_information ?? "no_reason_given");
}

function sentenceCase(words: string): string {
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** One side's four axes, in spec §6's order. */
function SideAssessment({
  side,
  condition,
}: {
  readonly side: Side;
  readonly condition: ConditionResponse;
}) {
  const headingId = useId();

  return (
    <section className={styles.company} aria-labelledby={headingId}>
      <h3 className={styles.companyHeading} id={headingId}>
        {sentenceCase(nameOf(side))}
      </h3>
      <Axis name="Centering">
        <Centering side={side} centering={condition.centering} />
      </Axis>
      <Axis name="Corners">
        <RegionFindings regions={condition.corners[side]} />
      </Axis>
      <Axis name="Edges">
        <RegionFindings regions={condition.edges[side]} />
      </Axis>
      <Axis name="Surface">
        <Surface surface={condition.surface[side]} />
      </Axis>
    </section>
  );
}

function Axis({ name, children }: { readonly name: string; readonly children: React.ReactNode }) {
  const headingId = useId();

  return (
    <section className={styles.question} aria-labelledby={headingId}>
      <h4 className={styles.questionHeading} id={headingId}>
        {name}
      </h4>
      {children}
    </section>
  );
}

/**
 * The side's two ratios, each measured or refused on its own (§21); the whole
 * axis refused is the one reason on every side.
 */
function Centering({
  side,
  centering,
}: {
  readonly side: Side;
  readonly centering: CenteringResponse | Refusal;
}) {
  if (isRefusal(centering)) return <Reason refusal={centering} />;

  // The wire names the four ratios by side; a side it does not name has none.
  const ratios =
    side === "front"
      ? ([centering.front_horizontal, centering.front_vertical] as const)
      : side === "back"
        ? ([centering.back_horizontal, centering.back_vertical] as const)
        : null;
  if (ratios === null) return <p className={styles.body}>Not measured on this side.</p>;
  const [horizontal, vertical] = ratios;

  return (
    <dl className={styles.facts}>
      <Ratio term="Left to right" ratio={horizontal} />
      <Ratio term="Top to bottom" ratio={vertical} />
    </dl>
  );
}

function Ratio({ term, ratio }: { readonly term: string; readonly ratio: number | Refusal }) {
  return isRefusal(ratio) ? (
    <Fact term={term} value={refusalCopy(ratio)} supplied={false} />
  ) : (
    <Fact term={term} value={ratioLabel(ratio)} />
  );
}

/** Four corners or four edges in the wire's order, or the side refused. */
function RegionFindings({ regions }: { readonly regions: Regions | Refusal | undefined }) {
  if (regions === undefined) return <p className={styles.body}>Not read on this side.</p>;
  if (isRefusal(regions)) return <Reason refusal={regions} />;

  return (
    <dl className={styles.facts}>
      {Object.entries(regions).map(([region, finding]) => (
        <Fact key={region} term={regionCopy(region)} value={findingCopy(finding)} />
      ))}
    </dl>
  );
}

/**
 * What was found, then what was not looked for — always both. §16 has no
 * `clean` class, so an empty finding list is said as "nothing found among the
 * classes that were looked for", and the classes that were not are listed
 * grouped by the reason they were refused (#185).
 *
 * The findings are **counted per class and severity**, never listed one by
 * one: the V1 analyzer reports every stain it segments as its own finding, so
 * a real front carries dozens, and with the coordinates rightly gone (#245)
 * they would be forty identical lines. A count is what a person can read and
 * is still the wire's — nothing is dropped and nothing is scored.
 */
function Surface({ surface }: { readonly surface: SurfaceResponse | Refusal | undefined }) {
  if (surface === undefined) return <p className={styles.body}>Not read on this side.</p>;
  if (isRefusal(surface)) return <Reason refusal={surface} />;

  const refused = new Map<string | null, string[]>();
  for (const [cls, refusal] of Object.entries(surface.not_assessed)) {
    const reason = refusal.insufficient_information;
    refused.set(reason, [...(refused.get(reason) ?? []), classCopy(cls)]);
  }
  const list = new Intl.ListFormat("en", { type: "conjunction" });

  return (
    <>
      {surface.findings.length === 0 ? (
        <p className={styles.body}>Nothing was found among the classes that were looked for.</p>
      ) : (
        <ul className={styles.faults}>
          {tally(surface.findings).map(([cls, counts]) => (
            <li key={cls}>
              {cls}: {counts}
            </li>
          ))}
        </ul>
      )}
      {refused.size > 0 && (
        <>
          <p className={styles.footnote}>Not looked for:</p>
          <ul className={styles.faults}>
            {[...refused].map(([reason, classes]) => (
              <li key={reason ?? ""}>
                {list.format(classes.map((cls, index) => (index === 0 ? cls : cls.toLowerCase())))}:{" "}
                {reasonCopy(reason ?? "no_reason_given")}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/** Findings counted per class, each class's counts by severity: `["Stains", "2 minor, 1 severe"]`. */
function tally(findings: readonly DefectResponse[]): (readonly [string, string])[] {
  const byClass = new Map<string, Map<Severity, number>>();
  for (const defect of findings) {
    const counts = byClass.get(defect.type) ?? new Map<Severity, number>();
    counts.set(defect.severity, (counts.get(defect.severity) ?? 0) + 1);
    byClass.set(defect.type, counts);
  }
  return [...byClass].map(([cls, counts]) => [
    classCopy(cls),
    SEVERITIES.filter((severity) => counts.has(severity))
      .map(
        (severity) =>
          `${String(counts.get(severity))} ${severity === null ? "could not be judged" : severityCopy(severity)}`,
      )
      .join(", "),
  ]);
}

/** A manufacturing defect as a finding; the caller names its side first. */
function defectCopy(defect: DefectResponse): string {
  return findingCopy({ label: defect.type, severity: defect.severity });
}

/** Derived across both sides (§17), so it sits after them and names the side per defect. */
function Manufacturing({ defects }: { readonly defects: DefectResponse[] | Refusal }) {
  const headingId = useId();

  return (
    <section className={styles.question} aria-labelledby={headingId}>
      <h4 className={styles.questionHeading} id={headingId}>
        Manufacturing defects
      </h4>
      {isRefusal(defects) ? (
        <Reason refusal={defects} />
      ) : defects.length === 0 ? (
        <p className={styles.body}>None found among the classes that were looked for.</p>
      ) : (
        <ul className={styles.faults}>
          {defects.map((defect, index) => (
            <li key={`${defect.side}-${defect.type}-${String(index)}`}>
              {sentenceCase(nameOf(defect.side))}: {defectCopy(defect).toLowerCase()}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Reason({ refusal }: { readonly refusal: Refusal }) {
  return <p className={styles.footnote}>{refusalCopy(refusal)}</p>;
}
