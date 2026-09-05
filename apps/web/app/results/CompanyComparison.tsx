import { useId } from "react";

import type { CompanyComparisonResponse } from "@/lib/api";
import { figureLabel, formatFigure, percentOf, reasonCopy } from "@/lib/results-copy";

import styles from "./page.module.css";

/**
 * Spec §49's second screen, "Compare PSA / TAG / BGS" — issue #248. Rendered
 * from `recommendation.comparison` and `refused`, and it decides nothing: the
 * order is the engine's under the mode the user chose on `/configure` (#63),
 * the reasons are the engine's and the models' (#238), and the words are
 * `lib/results-copy`'s.
 *
 * **Ranked is an `<ol>`, unranked is a `<ul>`.** The markup says what #63
 * says: an ordered list *is* the order, and a company with no place in it is
 * in a list with no order — apart, never appended as the last row. There is
 * no `<table>`, because the figure can differ per company under one mode
 * (`P(10)` beside `P(9_or_higher)`) and a single column header would say
 * otherwise; each company carries its own label instead.
 *
 * **A tie is said.** `tied_at_the_top` is alphabetical and means nothing, so
 * the sentence says so rather than letting the first card read as the winner.
 *
 * **`comparison: null` is the admission, and `refused` is its only per-company
 * carrier.** An empty `refused` beside it means the engine set every company
 * aside — in V1, because nothing is priced — not that nothing was refused.
 */
export function CompanyComparison({
  comparison,
  reason,
  refused,
  currency,
  displayName,
}: {
  readonly comparison: CompanyComparisonResponse | null;
  /** `comparison_reason`: why there is no comparison, when there is none. */
  readonly reason: string | null;
  /** `ResultsResponse.refused` as entries: the companies whose model declined. */
  readonly refused: readonly (readonly [string, string])[];
  readonly currency: string;
  readonly displayName: (slug: string) => string;
}) {
  const labelId = useId();

  if (comparison === null) {
    return (
      <>
        <p className={styles.body}>{reasonCopy(reason ?? "no_reason_given")}</p>
        {refused.length === 0 ? (
          <p className={styles.footnote}>
            No grading model declined to answer: the engine set every company aside because nothing
            was priced.
          </p>
        ) : (
          <Apart entries={refused} displayName={displayName} />
        )}
      </>
    );
  }

  const tied = comparison.tied_at_the_top;

  return (
    <>
      <h3 className={styles.modeHeading} id={labelId}>
        {comparison.label}
      </h3>
      {tied.length > 1 && (
        <p className={styles.body}>
          {new Intl.ListFormat("en", { type: "conjunction" }).format(tied.map(displayName))} are
          tied for first; the order between them is alphabetical and means nothing.
        </p>
      )}
      <ol className={styles.ranking} aria-labelledby={labelId}>
        {comparison.ranked.map((entry) => (
          <li className={styles.company} key={entry.company}>
            <h4 className={styles.companyHeading}>{displayName(entry.company)}</h4>
            <dl className={styles.facts}>
              <div className={styles.fact}>
                <dt className={styles.term}>{figureLabel(entry.figure)}</dt>
                <dd className={styles.value}>
                  {formatFigure(entry.figure, entry.value, currency)}
                </dd>
              </div>
              <div className={styles.fact}>
                <dt className={styles.term}>How far this figure is trusted</dt>
                <dd className={styles.value}>{percentOf(entry.confidence)}</dd>
              </div>
            </dl>
          </li>
        ))}
      </ol>
      {comparison.unranked.length > 0 && (
        <Apart
          entries={comparison.unranked.map((entry) => [entry.company, entry.reason] as const)}
          displayName={displayName}
        />
      )}
    </>
  );
}

/** The companies with no place in the order, each wearing its own reason. */
function Apart({
  entries,
  displayName,
}: {
  readonly entries: readonly (readonly [string, string])[];
  readonly displayName: (slug: string) => string;
}) {
  const headingId = useId();

  return (
    <>
      <h3 className={styles.modeHeading} id={headingId}>
        Could not be compared
      </h3>
      <ul className={styles.ranking} aria-labelledby={headingId}>
        {entries.map(([slug, reason]) => (
          <li className={styles.company} key={slug}>
            <h4 className={styles.companyHeading}>{displayName(slug)}</h4>
            <p className={styles.body}>{reasonCopy(reason)}</p>
          </li>
        ))}
      </ul>
    </>
  );
}
