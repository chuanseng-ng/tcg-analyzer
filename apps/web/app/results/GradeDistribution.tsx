import { useId } from "react";

import type { GradeProbabilityResponse } from "@/lib/api";

import styles from "./page.module.css";

/**
 * One company's grade distribution as horizontal bars — spec §49's third
 * priority, issue #247, and the one thing §2.1 says a screen must never throw
 * away: the whole distribution, not the grade it peaks at.
 *
 * **The chart is the table.** Spec §6 draws this block as two columns, grade
 * and percent, so the accessible twin `dataviz` requires and the chart are one
 * `<table>`: the grade is the row header, the bar and its percent are the cell.
 * A screen reader hears grade → percent down the ladder; nothing is hidden and
 * duplicated. The bar itself is `aria-hidden` because the cell's text already
 * says what its width says.
 *
 * **The ladder is the wire's.** Every term is a row in the order it arrived
 * (#228: ascending), and the key is shown as `GET /grading-companies` spells it
 * — `9.5` where a company issues one, a collapsed tail as its inequality. Nothing
 * here sorts, buckets, smooths or renormalises, and nothing here knows which
 * company it is drawing.
 *
 * `dataviz`: one series, one hue (`--color-series-1`, validated in both colour
 * schemes), so no legend — the caption names the series. Bars are thin, square
 * at the baseline and rounded at the data end, separated by the surface rather
 * than a stroke. Every row is labelled, which the skill would call over-labelling
 * on a chart and which is exactly right for a ≤ 19-row list the spec prints as
 * a table; with every value on screen, a tooltip would carry nothing.
 */
export function GradeDistribution({
  name,
  distribution,
}: {
  readonly name: string;
  readonly distribution: readonly GradeProbabilityResponse[];
}) {
  const captionId = useId();

  return (
    <figure className={styles.chart} aria-labelledby={captionId}>
      <figcaption className={styles.chartCaption} id={captionId}>
        {name} grade probabilities
      </figcaption>
      <table className={styles.ladder} aria-labelledby={captionId}>
        <tbody>
          {distribution.map(({ grade, probability }) => (
            <tr key={grade}>
              <th className={styles.grade} scope="row">
                {gradeLabel(grade)}
              </th>
              <td className={styles.probability}>
                {/* The row inside the cell, not the cell itself: a flex `<td>`
                    stops being laid out as a cell. */}
                <div className={styles.track}>
                  <span
                    className={styles.bar}
                    style={{ inlineSize: `${String(Number((probability * 100).toFixed(4)))}%` }}
                    data-bar
                    aria-hidden="true"
                  />
                  <span className={styles.percent}>{percentLabel(probability)}</span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

/** Spec §24's collapsed tails, as spec §6 prints them (`≤7`); any other key verbatim. */
const TAIL = /^(.+)_or_(lower|higher)$/;

export function gradeLabel(grade: string): string {
  const tail = TAIL.exec(grade);
  if (tail?.[1] === undefined) return grade;
  return `${tail[2] === "lower" ? "≤" : "≥"} ${tail[1]}`;
}

/**
 * A whole percent, as spec §6 prints them. A probability that is not zero but
 * rounds to it reads `<1%`: the bar is still drawn, and `0%` would say the
 * grade is impossible when the model said it is unlikely.
 */
export function percentLabel(probability: number): string {
  const percent = Math.round(probability * 100);
  if (percent === 0 && probability > 0) return "<1%";
  return `${String(percent)}%`;
}
