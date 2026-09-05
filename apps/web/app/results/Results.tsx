"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { proportionToPercent } from "@/lib/amount-input";
import { currentAnalysis } from "@/lib/analysis-session";
import { isFailed, isTerminal, stepCopy } from "@/lib/analysis-state";
import {
  getGradingCompanies,
  readAnalysis,
  readResults,
  type AnalysisResponse,
  type CompanyEconomicsResponse,
  type GradingCompanyResponse,
  type MarketSnapshotReference,
  type ReasonResponse,
  type RecommendationResponse,
  type ResultsResponse,
} from "@/lib/api";
import { concerning, faultsIn, isUnusable, nameOf } from "@/lib/quality-copy";
import {
  actionHeadline,
  figureLabel,
  formatFigure,
  percentOf,
  reasonCopy,
  signedAmount,
} from "@/lib/results-copy";
import { classifyResultsFailure, type ResultsFailure } from "@/lib/results-errors";

import { CompanyComparison } from "./CompanyComparison";
import { GradeDistribution } from "./GradeDistribution";
import styles from "./page.module.css";

/** Between polls — the cadence `/analyze` waits for the quality gate at. */
const POLL_INTERVAL_MS = 1_000;

type ImageQuality = AnalysisResponse["images"][number];

type State =
  | { readonly status: "no_analysis" }
  /** Not finished; `step` is what the analysis is doing, in words. */
  | { readonly status: "working"; readonly step: string }
  | { readonly status: "failed"; readonly analysis: AnalysisResponse }
  | { readonly status: "unavailable"; readonly failure: ResultsFailure }
  | {
      readonly status: "ready";
      readonly results: ResultsResponse;
      readonly analysis: AnalysisResponse;
      readonly companies: readonly GradingCompanyResponse[];
    };

/**
 * The results screen — spec §49's first two priorities, issue #246.
 *
 * **The analysis is read first, and the results once.** `GET /analyses/{id}` is
 * the endpoint §65 says a client polls, and `completed` — which the
 * configuration write reaches (#244) — means every input the results need is
 * recorded. Arriving from `/configure` the first read already says so, so the
 * poll is for a reload, a direct arrival and for `failed`. It runs at the
 * `/analyze` cadence and stops on a terminal state or when the screen is left;
 * the `AbortController` covers every request and the sleep timer is cleared,
 * so nothing sets state after unmount.
 *
 * **Two states that must never collapse.** `recommendation: null` is "nobody
 * has asked" — no configuration, or no stored prediction — and
 * `recommended_action: "insufficient_information"` is "asked, and the data did
 * not support an answer" (#65). The V1 answer is always the second, on
 * `grade_confidence_below_threshold` (0.35 against 0.50, #228), and this screen
 * shows it as an admission with its numbers rather than hiding it or inventing
 * a verdict; the companies' figures stay below it, not behind it.
 *
 * **`failed` is explained from the photographs.** The poll endpoint carries no
 * error envelope; `confirm-card`'s `_failed()` decides between
 * `image_quality_failure` and `analysis_failed` by whether any photograph is
 * `unusable`, and this screen applies the same rule to the same field rather
 * than making a second request to learn a code.
 *
 * Everything an amount is stays the decimal string the wire carried, prefixed
 * with the currency (#66); nothing here is a total (#58); and display names come
 * from `GET /grading-companies`, falling back to the slug when that list cannot
 * be read — a courtesy, not a gate, because the figures do not depend on it.
 */
export function Results() {
  const [state, setState] = useState<State>({
    status: "working",
    step: "Looking up the analysis.",
  });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    // Read in an effect rather than in `useState`: `sessionStorage` does not
    // exist while Next prerenders this, and a first render that disagreed with
    // the browser's would be a hydration mismatch.
    const analysisId = currentAnalysis();
    if (analysisId === null) {
      setState({ status: "no_analysis" });
      return;
    }

    const controller = new AbortController();
    const { signal } = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const pause = () =>
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, POLL_INTERVAL_MS);
      });

    setState({ status: "working", step: "Looking up the analysis." });

    // A listing that fails leaves the slugs on screen; it does not hold the
    // figures back.
    const companies = getGradingCompanies(signal).then(
      (listing) => listing.companies,
      (): readonly GradingCompanyResponse[] => [],
    );

    (async () => {
      let analysis = await readAnalysis(analysisId, signal);
      // ponytail: polls at 1 s for as long as the tab is open on an unfinished
      // analysis; add a cap or a backoff if a stuck one ever shows up in practice.
      while (!isTerminal(analysis.status)) {
        setState({ status: "working", step: stepCopy(analysis.status) });
        await pause();
        analysis = await readAnalysis(analysisId, signal);
      }
      if (isFailed(analysis.status)) {
        setState({ status: "failed", analysis });
        return;
      }
      setState({ status: "working", step: stepCopy(analysis.status) });
      const results = await readResults(analysisId, signal);
      setState({ status: "ready", results, analysis, companies: await companies });
    })().catch((error: unknown) => {
      if (!signal.aborted) {
        setState({ status: "unavailable", failure: classifyResultsFailure(error) });
      }
    });

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [attempt]);

  switch (state.status) {
    case "no_analysis":
      return <NoAnalysis />;
    case "working":
      return (
        <p className={styles.status} role="status" aria-live="polite">
          {state.step}
        </p>
      );
    case "failed":
      return <Failed analysis={state.analysis} />;
    case "unavailable":
      return (
        <Unavailable
          failure={state.failure}
          onRetry={() => setAttempt((previous) => previous + 1)}
        />
      );
    case "ready":
      return <Ready {...state} />;
  }
}

/**
 * Arriving with no analysis in this tab. There is nothing to read: the results
 * exist only against an analysis, and `/analyze` is where one begins.
 */
function NoAnalysis() {
  return (
    <div className={styles.gate}>
      <h1 className={styles.heading}>There are no results to show in this tab.</h1>
      <p className={styles.body}>
        Results belong to an analysis, and this tab has none — the photographs may have been taken
        in another tab, or this one may have been reopened since.
      </p>
      <div className={styles.actions}>
        <Link className={styles.action} href="/analyze">
          Photograph a card
        </Link>
      </div>
    </div>
  );
}

/**
 * §65 has no way out of `failed`, so this offers new photographs and nothing
 * else. A photograph the gate refused is named with the gate's own words
 * (`lib/quality-copy`); any other failure is said without blaming them, because
 * nothing here suggests they were the problem.
 */
function Failed({ analysis }: { readonly analysis: AnalysisResponse }) {
  const refused = analysis.images.filter(isUnusable);

  return (
    <div className={styles.failure} role="alert">
      <h1 className={styles.failureHeading}>This analysis could not be completed.</h1>
      {refused.length > 0 ? (
        refused.map((image) => (
          <div key={image.side}>
            <p className={styles.body}>
              The {nameOf(image.side)} photograph could not support an analysis.
            </p>
            <PhotographFaults image={image} />
          </div>
        ))
      ) : (
        <p className={styles.body}>
          This analysis did not finish, and nothing suggests the photographs were the problem.
        </p>
      )}
      <div className={styles.actions}>
        <Link className={styles.action} href="/analyze">
          Photograph the card again
        </Link>
      </div>
    </div>
  );
}

function PhotographFaults({ image }: { readonly image: ImageQuality }) {
  const faults = faultsIn(image);
  if (faults.length === 0) return null;
  return (
    <ul className={styles.faults}>
      {faults.map((fault) => (
        <li key={fault}>{fault}</li>
      ))}
    </ul>
  );
}

function Unavailable({
  failure,
  onRetry,
}: {
  readonly failure: ResultsFailure;
  readonly onRetry: () => void;
}) {
  return (
    <div className={styles.failure} role="alert">
      <h1 className={styles.failureHeading}>The results could not be read.</h1>
      <p className={styles.failureBody}>{failure.message}</p>
      <div className={styles.actions}>
        {failure.action === "restart" && (
          <Link className={styles.action} href="/analyze">
            Photograph a card
          </Link>
        )}
        {failure.action === "retry" && (
          <button className={styles.retry} type="button" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Spec §49's order: the recommendation, then the expected economic outcome, then
 * each company's grade distribution (#247), then the comparison (#248), then room
 * held for the condition (#249). The market snapshot sits under the figures it
 * priced, so no figure is ever shown without its date (ADR 0006). A refused
 * company has no distribution to chart and is named, with its reason, among the
 * figures above and again apart from the ranked companies below.
 */
function Ready({
  results,
  analysis,
  companies,
}: {
  readonly results: ResultsResponse;
  readonly analysis: AnalysisResponse;
  readonly companies: readonly GradingCompanyResponse[];
}) {
  const heading = useRef<HTMLHeadingElement>(null);

  // The waiting line unmounts with this, so without this a keyboard user is
  // dropped back at the top of the document with nothing announced.
  useEffect(() => {
    heading.current?.focus();
  }, []);

  const displayName = (slug: string) =>
    companies.find((company) => company.company === slug)?.display_name ?? slug;
  const refused = Object.entries(results.refused);

  return (
    <div className={styles.results}>
      {results.recommendation === null ? (
        <NotAsked heading={heading} />
      ) : (
        <Recommendation
          heading={heading}
          recommendation={results.recommendation}
          analysis={analysis}
          currency={results.currency}
          displayName={displayName}
        />
      )}

      <section className={styles.section} aria-labelledby="economics">
        <h2 className={styles.sectionHeading} id="economics">
          What grading is expected to come to
        </h2>
        {results.companies.length === 0 && refused.length === 0 && (
          <p className={styles.body}>No company has answered yet.</p>
        )}
        {results.companies.map((company) => (
          <Company
            key={company.company}
            company={company}
            currency={results.currency}
            name={displayName(company.company)}
          />
        ))}
        {refused.map(([slug, reason]) => (
          <article className={styles.company} key={slug}>
            <h3 className={styles.companyHeading}>{displayName(slug)}</h3>
            <p className={styles.body}>{reasonCopy(reason)}</p>
          </article>
        ))}
        <MarketStamp snapshot={results.market_snapshot} />
      </section>

      {results.companies.length > 0 && (
        <section className={styles.section} aria-labelledby="grades">
          <h2 className={styles.sectionHeading} id="grades">
            Grade probabilities
          </h2>
          {results.companies.map((company) => (
            <article className={styles.company} key={company.company}>
              <GradeDistribution
                name={displayName(company.company)}
                distribution={company.grade_distribution}
              />
              {/* The one number beside the chart (#247). Its threshold is the
                  engine's (#64), and the recommendation above already sets the
                  two side by side, so no "low" is decided here. */}
              <dl className={styles.facts}>
                <Fact
                  term={figureLabel("distribution_confidence")}
                  value={percentOf(company.distribution_confidence)}
                />
              </dl>
            </article>
          ))}
        </section>
      )}
      {/* Spec §49's second screen (#248). Nothing to compare, and no reason
          either, until something has been asked. */}
      {results.recommendation !== null && (
        <section className={styles.section} aria-labelledby="comparison">
          <h2 className={styles.sectionHeading} id="comparison">
            Company comparison
          </h2>
          <CompanyComparison
            comparison={results.recommendation.comparison}
            reason={results.recommendation.comparison_reason}
            refused={refused}
            currency={results.currency}
            displayName={displayName}
          />
        </section>
      )}
      <Placeholder heading="Condition">
        What was read off the card&apos;s centering, corners, edges and surface arrives with #249.
      </Placeholder>
    </div>
  );
}

/** `recommendation: null`: nobody has asked. Never the admission's words (#65). */
function NotAsked({ heading }: { readonly heading: React.RefObject<HTMLHeadingElement | null> }) {
  return (
    <div className={styles.gate} role="status">
      <h1 className={styles.heading} ref={heading} tabIndex={-1}>
        Nothing has been asked of this analysis yet.
      </h1>
      <p className={styles.body}>
        A recommendation needs the costs to be set and a grade to be predicted, and one of those is
        not recorded yet. Nothing has declined to answer — the question has not been put.
      </p>
    </div>
  );
}

function Recommendation({
  heading,
  recommendation,
  analysis,
  currency,
  displayName,
}: {
  readonly heading: React.RefObject<HTMLHeadingElement | null>;
  readonly recommendation: RecommendationResponse;
  readonly analysis: AnalysisResponse;
  readonly currency: string;
  readonly displayName: (slug: string) => string;
}) {
  const qualityFailed = recommendation.failed_gates.some(
    (gate) => gate.code === "image_quality_below_threshold",
  );

  return (
    <div className={styles.recommendation}>
      <h1 className={styles.heading} ref={heading} tabIndex={-1}>
        {actionHeadline(recommendation.recommended_action)}
      </h1>

      <dl className={styles.facts}>
        {/* `null` beside an admission is deliberate (#64): naming a company
            there would be the forced recommendation §44 forbids. */}
        <Fact
          term="Company"
          value={
            recommendation.recommended_company === null
              ? "No company"
              : displayName(recommendation.recommended_company)
          }
          supplied={recommendation.recommended_company !== null}
        />
        <Fact term="Confidence in this answer" value={percentOf(recommendation.confidence)} />
        <Fact term="Photograph quality" value={percentOf(recommendation.image_quality)} />
      </dl>

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Why</h2>
        <Reason reason={recommendation.reason} currency={currency} />
      </section>

      {recommendation.failed_gates.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>Every check that did not pass</h2>
          <ul className={styles.gates}>
            {recommendation.failed_gates.map((gate, index) => (
              <li key={`${gate.code}-${String(index)}`}>
                <Reason reason={gate} currency={currency} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {qualityFailed &&
        concerning(analysis.images).map((image) => (
          <div key={image.side}>
            <p className={styles.body}>The {nameOf(image.side)} photograph:</p>
            <PhotographFaults image={image} />
          </div>
        ))}
    </div>
  );
}

/**
 * Spec §44's `reason` as three things a person can check — what was measured,
 * what it came to, what it needed to clear — and the sentence for the code.
 * A propagated admission has no number (#64), and none is invented for it.
 */
function Reason({
  reason,
  currency,
}: {
  readonly reason: ReasonResponse;
  readonly currency: string;
}) {
  const value = formatFigure(reason.figure, reason.value, currency);
  const threshold = formatFigure(reason.figure, reason.threshold, currency);

  return (
    <div className={styles.reason}>
      <dl className={styles.facts}>
        <Fact term="What was measured" value={figureLabel(reason.figure)} />
        <Fact term="What it came to" value={value ?? "No figure"} supplied={value !== null} />
        {threshold !== null && <Fact term="What it needed to clear" value={threshold} />}
      </dl>
      <p className={styles.body}>{reasonCopy(reason.code)}</p>
    </div>
  );
}

/**
 * One company's §41 figures, the two questions named apart as `/configure`
 * named them (#66). Each figure is present-and-null beside its own reason on
 * the wire, and is rendered as its reason — never as a number — when null.
 */
function Company({
  company,
  currency,
  name,
}: {
  readonly company: CompanyEconomicsResponse;
  readonly currency: string;
  readonly name: string;
}) {
  const decision = company.incremental_grading_decision;
  const investment = company.investment_return;
  const expected = company.expected_graded_value;

  return (
    <article className={styles.company}>
      <h3 className={styles.companyHeading}>{name}</h3>

      <section className={styles.question}>
        <h4 className={styles.questionHeading}>Is it worth grading this card?</h4>
        <dl className={styles.facts}>
          <Figure
            term={figureLabel("incremental_profit")}
            value={decision === null ? null : signedAmount(decision.incremental_profit, currency)}
            reason={company.incremental_reason}
          />
          <Figure
            term={company.incremental_roi?.label ?? figureLabel("incremental_roi")}
            value={
              company.incremental_roi === null
                ? null
                : `${proportionToPercent(company.incremental_roi.value)}%`
            }
            reason={company.incremental_roi_reason}
          />
        </dl>
        {decision !== null && <Unpriced figure={decision} />}
      </section>

      <section className={styles.question}>
        <h4 className={styles.questionHeading}>Did buying this card make money?</h4>
        <dl className={styles.facts}>
          <Figure
            term="Money made on buying it to grade"
            value={
              investment === null ? null : signedAmount(investment.investment_profit, currency)
            }
            reason={company.investment_reason}
          />
          <Figure
            term={company.investment_roi?.label ?? "Return on what was paid"}
            value={
              company.investment_roi === null
                ? null
                : `${proportionToPercent(company.investment_roi.value)}%`
            }
            reason={company.investment_roi_reason}
          />
        </dl>
      </section>

      <dl className={styles.facts}>
        <Figure
          term={figureLabel("graded_proceeds")}
          value={expected === null ? null : `${currency} ${expected.amount}`}
          reason={company.expected_graded_value_reason}
        />
      </dl>
    </article>
  );
}

/** How much of the distribution the figure could not price — said only when some of it. */
function Unpriced({
  figure,
}: {
  readonly figure: { readonly unpriced_probability: number; readonly unpriced_grades: string[] };
}) {
  if (figure.unpriced_probability <= 0) return null;
  return (
    <p className={styles.footnote}>
      {percentOf(figure.unpriced_probability)} of the likely grades had no price (
      {figure.unpriced_grades.join(", ")}), so these figures set them aside rather than counting
      them as nothing.
    </p>
  );
}

function Fact({
  term,
  value,
  supplied = true,
}: {
  readonly term: string;
  readonly value: string;
  readonly supplied?: boolean;
}) {
  return (
    <div className={styles.fact}>
      <dt className={styles.term}>{term}</dt>
      <dd className={styles.value} data-supplied={supplied}>
        {value}
      </dd>
    </div>
  );
}

/** A figure, or the reason there is none. An unknown reason is still named. */
function Figure({
  term,
  value,
  reason,
}: {
  readonly term: string;
  readonly value: string | null;
  readonly reason: string | null;
}) {
  return (
    <Fact
      term={term}
      value={value ?? reasonCopy(reason ?? "no_reason_given")}
      supplied={value !== null}
    />
  );
}

/**
 * ADR 0006's condition: a dated record of a past market is honest, and the
 * same numbers presented as current are not. No snapshot is said in words, so
 * no figure above is ever undated.
 */
function MarketStamp({ snapshot }: { readonly snapshot: MarketSnapshotReference | null }) {
  if (snapshot === null) {
    return (
      <p className={styles.footnote}>
        No market data was recorded for this analysis, so nothing above is priced.
      </p>
    );
  }
  const taken = new Date(snapshot.generated_at).toLocaleDateString("en-SG", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  return (
    <p className={styles.footnote}>
      Prices are from the market snapshot of {snapshot.data_version}, taken{" "}
      <time dateTime={snapshot.generated_at}>{taken}</time>.
    </p>
  );
}

function Placeholder({
  heading,
  children,
}: {
  readonly heading: string;
  readonly children: React.ReactNode;
}) {
  return (
    <section className={styles.placeholder} aria-labelledby={`placeholder-${heading}`}>
      <h2 className={styles.sectionHeading} id={`placeholder-${heading}`}>
        {heading}
      </h2>
      <p>{children}</p>
    </section>
  );
}
