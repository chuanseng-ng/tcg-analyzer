"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { GradeDistribution } from "@/app/results/GradeDistribution";
import {
  getGradingCompanies,
  readFeedback,
  submitFeedback,
  type FeedbackSnapshotResponse,
  type GradeProbabilityResponse,
  type GradingCompanyResponse,
  type ReportedGradeResponse,
} from "@/lib/api";
import { classifyFeedbackFailure, type FeedbackFailure } from "@/lib/feedback-errors";
import { actionHeadline, reasonCopy } from "@/lib/results-copy";

import styles from "./page.module.css";

/**
 * Spec §68's question, asked of somebody holding a code — issue #274.
 *
 * Two reads and one write, all addressed by the code alone: what was predicted
 * (`GET /feedback/{code}`), the companies and their scales
 * (`GET /grading-companies`), and the answer (`POST /feedback/{code}`).
 *
 * **A spent code is a 404**, so the confirmation is rendered from the `POST`'s
 * own body. There is nothing to re-read afterwards, and there is no edit path:
 * a wrong answer is a new code from a new analysis.
 *
 * **Unknown, expired, already-answered and malformed are one screen**, because
 * the service tells them apart nowhere — a well-formed guess must learn nothing
 * a malformed one would not. So this page lists the reasons a code stops
 * working rather than claiming one of them.
 */
type State =
  | { readonly status: "loading" }
  | {
      readonly status: "asking";
      readonly snapshot: FeedbackSnapshotResponse;
      readonly companies: readonly GradingCompanyResponse[];
      readonly saving: boolean;
      readonly failure?: FeedbackFailure;
    }
  | { readonly status: "recorded"; readonly answer: ReportedGradeResponse; readonly name: string }
  | { readonly status: "gone" }
  | { readonly status: "unavailable"; readonly failure: FeedbackFailure };

export function ReportGrade({ code }: { readonly code: string }) {
  const [state, setState] = useState<State>({ status: "loading" });
  const [company, setCompany] = useState<string>("");
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds((left) => left - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ status: "loading" });

    Promise.all([readFeedback(code, controller.signal), getGradingCompanies(controller.signal)])
      .then(([snapshot, listing]) => {
        if (!active) return;
        setState({ status: "asking", snapshot, companies: listing.companies, saving: false });
        setCompany((chosen) => chosen || (listing.companies[0]?.company ?? ""));
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        const failure = classifyFeedbackFailure(error);
        // A code that addresses nothing is the page, not an error on it.
        setState(
          failure.action === "gone" ? { status: "gone" } : { status: "unavailable", failure },
        );
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [code, attempt]);

  const submit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (state.status !== "asking" || state.saving || waitSeconds > 0) return;

      const form = new FormData(event.currentTarget);
      const gradingCompany = String(form.get("grading_company") ?? "");
      const grade = String(form.get("grade") ?? "");
      const certification = String(form.get("certification_number") ?? "").trim();
      if (gradingCompany === "" || grade === "") return;

      const { snapshot, companies } = state;
      const name = displayNameIn(companies, gradingCompany);
      setState({ status: "asking", snapshot, companies, saving: true });

      submitFeedback(code, {
        grading_company: gradingCompany,
        grade,
        // Omitted rather than sent blank: it is optional on the wire, and an
        // empty string is a value where absence is the answer.
        ...(certification === "" ? {} : { certification_number: certification }),
      })
        .then((answer) => {
          setState({ status: "recorded", answer, name });
        })
        .catch((error: unknown) => {
          const failure = classifyFeedbackFailure(error);
          if (failure.retryAfterSeconds !== undefined) setWaitSeconds(failure.retryAfterSeconds);
          // The code was spent between opening the page and answering, or it
          // expired in between. The same screen an unknown code gets.
          setState(
            failure.action === "gone"
              ? { status: "gone" }
              : { status: "asking", snapshot, companies, saving: false, failure },
          );
        });
    },
    [code, state, waitSeconds],
  );

  if (state.status === "loading") {
    return (
      <p className={styles.status} role="status" aria-live="polite">
        Looking up what was predicted…
      </p>
    );
  }

  if (state.status === "gone") {
    return <Gone />;
  }

  if (state.status === "unavailable") {
    return (
      <div className={styles.failure} role="alert">
        <h1 className={styles.heading}>The prediction could not be read.</h1>
        <p className={styles.body}>{state.failure.message}</p>
        {state.failure.action === "retry" && (
          <button
            className={styles.retry}
            type="button"
            onClick={() => setAttempt((previous) => previous + 1)}
          >
            Try again
          </button>
        )}
      </div>
    );
  }

  if (state.status === "recorded") {
    return <Recorded answer={state.answer} name={state.name} />;
  }

  const chosen = state.companies.find((candidate) => candidate.company === company);

  return (
    <div className={styles.screen}>
      <h1 className={styles.heading}>What grade did it actually get?</h1>
      <p className={styles.body}>
        This is what was predicted for the card, {mintedOn(state.snapshot.created_at)}. Tell us what
        the slab came back as and it becomes a record of how close the prediction was — read by a
        person, never fed straight back into a model.
      </p>

      <Predicted snapshot={state.snapshot} companies={state.companies} />

      <form className={styles.form} onSubmit={submit}>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="grading_company">
            Which company graded it
          </label>
          <select
            className={styles.control}
            id="grading_company"
            name="grading_company"
            value={company}
            // Changing the company resets the grade, because the ladders differ
            // — BGS issues a 9.5 and the other two do not.
            onChange={(event) => setCompany(event.target.value)}
          >
            {state.companies.map((candidate) => (
              <option key={candidate.company} value={candidate.company}>
                {candidate.display_name}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="grade">
            The grade on the slab
          </label>
          {/* The options are that company's own scale, as `GET
              /grading-companies` sends it. Nothing here knows a ladder, which
              is also what keeps a grade off the scale from becoming a 422 this
              form could have prevented. `key` remounts the select so a company
              change cannot leave the previous ladder's grade selected. */}
          <select className={styles.control} id="grade" name="grade" key={company}>
            {(chosen?.grades ?? []).map((grade) => (
              <option key={grade} value={grade}>
                {grade}
              </option>
            ))}
          </select>
          {/* ponytail: grades only. A slab that comes back with a designation
              instead of a grade — PSA's `authentic` — is a real outcome the
              wire accepts and this form cannot express, because
              `GET /grading-companies` serves no designation list and #274
              forbids a client copy of one. Serve them there and this becomes
              one more group of options in the same select. */}
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="certification_number">
            The certification number, if you have it
          </label>
          <input
            className={styles.control}
            id="certification_number"
            name="certification_number"
            type="text"
            inputMode="numeric"
            autoComplete="off"
          />
          <p className={styles.footnote}>Optional. It is printed on the slab.</p>
        </div>

        <div className={styles.actions}>
          <button
            className={styles.action}
            type="submit"
            disabled={state.saving || waitSeconds > 0 || chosen === undefined}
          >
            {state.saving ? "Sending…" : "Send the grade"}
          </button>
        </div>

        {state.failure !== undefined && (
          <p className={styles.footnote} role="alert">
            {state.failure.action === "wait" && waitSeconds > 0
              ? `${state.failure.message} Try again in ${String(waitSeconds)}s.`
              : state.failure.message}
          </p>
        )}
      </form>
    </div>
  );
}

/** A code that addresses nothing, for all four reasons at once. */
function Gone() {
  return (
    <div className={styles.screen}>
      <h1 className={styles.heading}>No prediction is recorded under that code.</h1>
      <p className={styles.body}>A code stops working for one of four reasons:</p>
      <ul className={styles.reasons}>
        <li>it was already answered — each code is good for one grade;</li>
        <li>it has expired, which every code does after about six months;</li>
        <li>it was mistyped;</li>
        <li>it was never one of ours.</li>
      </ul>
      <p className={styles.body}>
        There is no way to look one up: the code is all we keep of the prediction, and we keep only
        its fingerprint. Analysing the card again gives a fresh prediction and a fresh code.
      </p>
      <div className={styles.actions}>
        <Link className={styles.action} href="/analyze">
          Photograph a card
        </Link>
      </div>
    </div>
  );
}

/** What was recorded, said back — the `POST`'s own body, because the code is now spent. */
function Recorded({
  answer,
  name,
}: {
  readonly answer: ReportedGradeResponse;
  readonly name: string;
}) {
  return (
    <div className={styles.screen}>
      <h1 className={styles.heading}>Thank you — that is recorded.</h1>
      <p className={styles.body}>
        {name} {answer.grade ?? answer.designation}
        {answer.certification_number === null
          ? ""
          : `, certification ${answer.certification_number}`}
        . A person reads it before it is used for anything, and the code is now spent.
      </p>
      <div className={styles.actions}>
        <Link className={styles.action} href="/analyze">
          Photograph another card
        </Link>
      </div>
    </div>
  );
}

/**
 * What was predicted, per company, as the results screen drew it.
 *
 * The chart is `/results`' own (#247) — the same table, the same ladder rules —
 * because this is that same distribution, seen later. A company whose model
 * refused carries its stored reason through the one copy table (#249's rule:
 * the reason is looked up exactly as the analyzer stored it).
 */
function Predicted({
  snapshot,
  companies,
}: {
  readonly snapshot: FeedbackSnapshotResponse;
  readonly companies: readonly GradingCompanyResponse[];
}) {
  const predictions = predictionsIn(snapshot.predictions);

  return (
    <section className={styles.predicted} aria-labelledby="predicted">
      <h2 className={styles.sectionHeading} id="predicted">
        What was predicted
      </h2>
      {snapshot.recommended_action !== null && (
        <p className={styles.body}>{actionHeadline(snapshot.recommended_action)}</p>
      )}
      {predictions.length === 0 && <p className={styles.body}>Nothing was predicted.</p>}
      {predictions.map(([slug, entry]) => {
        const company = companies.find((candidate) => candidate.company === slug);
        const name = company?.display_name ?? slug;
        const distribution = ladder(entry, company?.grades ?? []);
        return (
          <article className={styles.company} key={slug}>
            {distribution.length === 0 ? (
              <>
                <h3 className={styles.companyHeading}>{name}</h3>
                <p className={styles.body}>{reasonCopy(refusalIn(entry))}</p>
              </>
            ) : (
              <GradeDistribution name={name} distribution={distribution} />
            )}
          </article>
        );
      })}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Reading the stored document
// ---------------------------------------------------------------------------
/**
 * `predictions` is `analyses.grade_predictions` as the worker wrote it (#227),
 * copied whole at mint time — an envelope of `version`, `thresholds` and a
 * `predictions` map keyed by company slug. It is deliberately untyped on the
 * wire: it is a stored artifact rather than a response model, so everything
 * below narrows instead of trusting.
 */
function predictionsIn(document: unknown): [string, Record<string, unknown>][] {
  if (!isRecord(document) || !isRecord(document.predictions)) return [];
  return Object.entries(document.predictions).filter(
    (entry): entry is [string, Record<string, unknown>] => isRecord(entry[1]),
  );
}

function refusalIn(entry: Record<string, unknown>): string {
  const reason = entry.insufficient_information;
  return typeof reason === "string" ? reason : "insufficient_information";
}

/**
 * One company's distribution as an ordered ladder.
 *
 * **The order comes from the company's scale, never from the document.** The
 * stored form is a `{grade: probability}` mapping, and a JavaScript object puts
 * integer-like keys first in numeric order — `"10"` lands before `"1.5"` — so
 * iterating it would draw a scrambled ladder. `GET /grading-companies`' `grades`
 * is the wire's own order, which is what keeps #247's rule intact: nothing in
 * this app sorts a distribution. A term the scale does not name is appended
 * rather than dropped, because a company that changed its scale in the 180 days
 * since the code was minted must not silently renormalise a chart.
 */
function ladder(
  entry: Record<string, unknown>,
  grades: readonly string[],
): GradeProbabilityResponse[] {
  if (!isRecord(entry.distribution)) return [];
  const { distribution } = entry;
  const ordered: GradeProbabilityResponse[] = [];
  const seen = new Set<string>();

  for (const grade of grades) {
    const probability = distribution[grade];
    if (typeof probability === "number") {
      ordered.push({ grade, probability });
      seen.add(grade);
    }
  }
  for (const [grade, probability] of Object.entries(distribution)) {
    if (!seen.has(grade) && typeof probability === "number") {
      ordered.push({ grade, probability });
    }
  }
  return ordered;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayNameIn(companies: readonly GradingCompanyResponse[], slug: string): string {
  return companies.find((candidate) => candidate.company === slug)?.display_name ?? slug;
}

/** When the code was minted, in the reader's locale. */
function mintedOn(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "when it was analysed";
  const day = parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
  return `analysed on ${day}`;
}
