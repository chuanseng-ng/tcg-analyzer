"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { parseAmount, parsePercent, proportionToPercent } from "@/lib/amount-input";
import { currentAnalysis } from "@/lib/analysis-session";
import {
  configureEconomics,
  getGradingCompanies,
  type EconomicConfigurationRequest,
  type EconomicConfigurationResponse,
  type GradingCompanyResponse,
} from "@/lib/api";
import {
  classifyCompaniesFailure,
  classifyConfigureFailure,
  type CompaniesFailure,
  type ConfigureFailure,
} from "@/lib/economics-errors";

import styles from "./page.module.css";

/**
 * Spec §43's five modes, with the sentence each one needs.
 *
 * The slugs are the API's; the copy is this screen's. §43 lists the modes and
 * nothing else, the request takes `optimization_mode` as a bare string so a
 * sixth mode costs no schema change, and the engine's own labels are written for
 * a comparison table rather than for a choice.
 *
 * **Two of these ignore money, and both say so.** "Best chance of the top grade"
 * sounds like the obviously right answer until it names a company that loses you
 * money, and "cheapest to submit" is a company chosen without looking at what the
 * card is worth. A mode picked without knowing that is spec §42's casual choice,
 * one layer up.
 */
const MODES = [
  {
    mode: "expected_profit",
    label: "Most money made",
    explanation:
      "Ranks the companies by how much more you would make grading this card than selling it as it is.",
  },
  {
    mode: "roi",
    label: "Best return on what you risk",
    explanation:
      "Ranks by that same profit against the money tied up — the raw sale you give up, plus what grading costs. It is a smaller number than sites that count only the grading fee.",
  },
  {
    mode: "highest_grade_probability",
    label: "Best chance of the top grade",
    explanation:
      "Ranks on the odds alone. It does not look at money at all, so it can name a company that loses you some.",
  },
  {
    mode: "lowest_total_cost",
    label: "Cheapest to submit",
    explanation:
      "Ranks on what submitting costs and nothing else. It does not look at what the card is worth, so the cheapest company need not be the best one.",
  },
  {
    mode: "expected_graded_value",
    label: "Highest value once graded",
    explanation:
      "Ranks on what the graded card should fetch, before anything it costs you to get there.",
  },
] as const;

const DEFAULT_MODE = MODES[0].mode;

/**
 * How long the recorded figures stay on screen before the results.
 *
 * `/identify`'s pause, for the same reason: long enough to read the figures
 * back — this is the only place the standard costs are ever seen — rather than
 * a flash on the way past. The link below is live the whole time.
 */
const ADVANCE_AFTER_MS = 4_000;

/**
 * Five of spec §46's six line items. The sixth, the selling fee, is asked for
 * separately below because it is a proportion of a sale rather than an amount —
 * and because ADR 0007 nets it out of proceeds rather than committing it up
 * front, which makes it a different kind of cost.
 *
 * §48's field list says "Shipping" once where §46 has two line items. They stay
 * separate here because they are separate in the model, they differ in practice,
 * and a user quoted one and not the other has somewhere to put it.
 */
const COST_FIELDS = [
  { name: "grading_fee", label: "Grading fee" },
  { name: "outbound_shipping", label: "Shipping to the grader" },
  { name: "return_shipping", label: "Shipping back" },
  { name: "insurance", label: "Insurance" },
  { name: "miscellaneous", label: "Anything else" },
] as const;

type CostField = (typeof COST_FIELDS)[number]["name"];

/** Every field this form can complain about, beside the field itself. */
type FieldName = CostField | "acquisition_cost" | "selling_fee_rate" | "selling_fee_flat";

type Problems = Partial<Record<FieldName | "grading_companies", string>>;

type State =
  | { readonly status: "no_analysis" }
  | { readonly status: "loading" }
  | { readonly status: "unavailable"; readonly failure: CompaniesFailure }
  | {
      readonly status: "editing";
      readonly companies: readonly GradingCompanyResponse[];
      readonly saving: boolean;
      /** A previous attempt to record this configuration, if one failed. */
      readonly failure?: ConfigureFailure;
    }
  | {
      readonly status: "recorded";
      readonly configuration: EconomicConfigurationResponse;
      readonly companies: readonly GradingCompanyResponse[];
    };

const AMOUNT_PROBLEM = "Amounts are digits and at most two decimal places — 39.95, not S$39.95.";
const PERCENT_PROBLEM = "A percentage between 0 and 100, with at most two decimal places.";

/** DOM ids are kebab-case where the wire's field names are not. */
function fieldId(name: string): string {
  return name.replace(/_/g, "-");
}

/**
 * Price the decision (spec §45, §46, §43, §48).
 *
 * Three things about this screen are structural rather than cosmetic:
 *
 * **Blank means unknown, and never zero.** Spec §45 makes the acquisition cost
 * optional and forbids inferring it. A field pre-filled with `0.00` would turn
 * "I don't remember what I paid" into "it was free", and the investment return
 * computed from that is not merely imprecise — it is a different, confidently
 * wrong answer. So the field starts empty, has no placeholder amount, and a
 * blank one reaches the wire as `null`. `"0.00"` typed in is a real acquisition
 * cost and is sent as one.
 *
 * **The costs carry no defaults of their own.** Every cost field on the request
 * is optional and the endpoint fills it from the engine's `CostConfiguration`,
 * which is the single place those figures are written down. Restating them here
 * would be a second copy that drifts from the one the recommendation is actually
 * computed against — silently, and in the direction of the user's money. So the
 * fields are blank, an untouched form sends no `costs` key at all, and the
 * figures that were used are read back off the 201 and shown on the way out.
 *
 * **Two questions, named apart.** Spec §45 requires the market grading decision
 * and the investment return to be distinguished rather than conflated, and the
 * acquisition cost is the whole difference between them. The section that asks
 * for it is where that gets said, in the words a collector would use.
 *
 * The form is uncontrolled — values live in the DOM and are read from `FormData`
 * on submit, as `/cards` does — so re-rendering to show `saving` cannot disturb
 * what the user typed.
 */
export function EconomicConfiguration() {
  const [state, setState] = useState<State>({ status: "loading" });
  const [problems, setProblems] = useState<Problems>({});
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const analysisId = useRef<string | null>(null);

  // A throttled submission is counted down rather than offered a button that
  // would fire straight back into the limit (ADR 0005).
  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds((left) => left - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  useEffect(() => {
    // Read in an effect rather than in `useState`: `sessionStorage` does not
    // exist while Next prerenders this, and a first render that disagreed with
    // the browser's would be a hydration mismatch.
    analysisId.current = currentAnalysis();
    if (analysisId.current === null) {
      setState({ status: "no_analysis" });
      return;
    }

    const controller = new AbortController();
    let active = true;
    setState({ status: "loading" });

    getGradingCompanies(controller.signal)
      .then((listing) => {
        if (active) {
          setState({ status: "editing", companies: listing.companies, saving: false });
        }
      })
      .catch((error: unknown) => {
        if (active && !controller.signal.aborted) {
          setState({ status: "unavailable", failure: classifyCompaniesFailure(error) });
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [attempt]);

  const submit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (state.status !== "editing" || state.saving || waitSeconds > 0) return;

      const form = new FormData(event.currentTarget);
      const raw = (name: string) => String(form.get(name) ?? "");

      const found: Problems = {};

      // Blank is `null`, which is what "I don't know" has to reach the wire as.
      const acquisition = parseAmount(raw("acquisition_cost"));
      if (acquisition.state === "invalid") found.acquisition_cost = AMOUNT_PROBLEM;

      const costs: NonNullable<EconomicConfigurationRequest["costs"]> = {};
      for (const field of COST_FIELDS) {
        const parsed = parseAmount(raw(field.name));
        if (parsed.state === "invalid") found[field.name] = AMOUNT_PROBLEM;
        if (parsed.state === "amount") costs[field.name] = parsed.value;
      }

      const rate = parsePercent(raw("selling_fee_rate"));
      if (rate.state === "invalid") found.selling_fee_rate = PERCENT_PROBLEM;
      const flat = parseAmount(raw("selling_fee_flat"));
      if (flat.state === "invalid") found.selling_fee_flat = AMOUNT_PROBLEM;
      if (rate.state === "amount" || flat.state === "amount") {
        costs.selling_fee = {
          ...(rate.state === "amount" ? { rate: rate.value } : {}),
          ...(flat.state === "amount" ? { flat: flat.value } : {}),
        };
      }

      const chosen = form.getAll("grading_companies").map(String);
      if (chosen.length === 0) {
        found.grading_companies = "Choose at least one company to compare.";
      }

      setProblems(found);
      // Refused here rather than by the service: spec §66 has no code for a
      // malformed request, so a 422 arrives as FastAPI's own body with nothing
      // in it worth showing a person.
      if (Object.keys(found).length > 0) return;

      const request: EconomicConfigurationRequest = {
        // Present-and-null, not omitted: the absence is the answer, and saying
        // it out loud is what stops it reading as an oversight.
        acquisition_cost: acquisition.state === "amount" ? acquisition.value : null,
        // Omitted entirely when nothing was typed, so every default comes from
        // the endpoint rather than from this file.
        ...(Object.keys(costs).length > 0 ? { costs } : {}),
        grading_companies: chosen,
        optimization_mode: raw("optimization_mode") || DEFAULT_MODE,
      };

      const { companies } = state;
      const id = analysisId.current;
      if (id === null) {
        setState({ status: "no_analysis" });
        return;
      }

      setState({ status: "editing", companies, saving: true });
      configureEconomics(id, request)
        .then((configuration) => {
          setState({ status: "recorded", configuration, companies });
        })
        .catch((error: unknown) => {
          const failure = classifyConfigureFailure(error);
          if (failure.retryAfterSeconds !== undefined) setWaitSeconds(failure.retryAfterSeconds);
          setState({ status: "editing", companies, saving: false, failure });
        });
    },
    [state, waitSeconds],
  );

  if (state.status === "no_analysis") {
    return <NoAnalysis />;
  }

  if (state.status === "loading") {
    return (
      <p className={styles.status} role="status" aria-live="polite">
        Getting the grading companies…
      </p>
    );
  }

  if (state.status === "unavailable") {
    return (
      <Unavailable failure={state.failure} onRetry={() => setAttempt((previous) => previous + 1)} />
    );
  }

  if (state.status === "recorded") {
    return <Recorded configuration={state.configuration} companies={state.companies} />;
  }

  return (
    <Form
      companies={state.companies}
      saving={state.saving}
      failure={state.failure}
      problems={problems}
      waitSeconds={waitSeconds}
      onSubmit={submit}
    />
  );
}

/**
 * Arriving with no analysis in this tab.
 *
 * Unlike the confirmation gate, there is no page-local fallback: a configuration
 * exists only against an analysis, so there is nothing here to record it on, and
 * a form that took the answers anyway would be one that threw them away.
 */
function NoAnalysis() {
  return (
    <div className={styles.gate}>
      <h1 className={styles.heading}>There is no card to price in this tab.</h1>
      <p className={styles.body}>
        These figures are recorded against an analysis, and this tab has none — the photographs may
        have been taken in another tab, or this one may have been reopened since.
      </p>
      <div className={styles.actions}>
        <Link className={styles.submit} href="/analyze">
          Photograph a card
        </Link>
      </div>
    </div>
  );
}

function Unavailable({
  failure,
  onRetry,
}: {
  readonly failure: CompaniesFailure;
  readonly onRetry: () => void;
}) {
  return (
    <div className={styles.failure} role="alert">
      <h1 className={styles.failureHeading}>
        {failure === "unreachable"
          ? "The grading companies could not be listed right now."
          : "The grading companies could not be read."}
      </h1>
      <p className={styles.failureBody}>
        {failure === "unreachable"
          ? "The service is not answering. Nothing has been recorded — try again in a moment."
          : "The service answered with something this page did not understand."}
      </p>
      <p className={styles.failureBody}>
        PSA, TAG and BGS grade on different scales, so this form waits for the real list rather than
        guessing at one.
      </p>
      <div className={styles.failureActions}>
        <button className={styles.retry} type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

function Problem({ id, message }: { readonly id: string; readonly message: string | undefined }) {
  if (message === undefined) return null;
  return (
    <p className={styles.problem} id={id} role="alert">
      {message}
    </p>
  );
}

function Form({
  companies,
  saving,
  failure,
  problems,
  waitSeconds,
  onSubmit,
}: {
  readonly companies: readonly GradingCompanyResponse[];
  readonly saving: boolean;
  readonly failure: ConfigureFailure | undefined;
  readonly problems: Problems;
  readonly waitSeconds: number;
  readonly onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  // `gone` means there is nothing left to configure, so offering the button
  // again would be offering one that cannot work.
  const offerable = failure?.action !== "gone" && waitSeconds <= 0;

  return (
    <form className={styles.form} onSubmit={onSubmit} noValidate>
      <h1 className={styles.heading}>What would grading this card cost you?</h1>

      {/*
       * Spec §45: the market grading decision and the investment return are two
       * questions and must not be conflated. Said here, before anything is asked
       * for, because the next field is the entire difference between them.
       */}
      <section className={styles.questions}>
        <h2 className={styles.sectionHeading}>Two different questions</h2>
        <dl className={styles.questionList}>
          <div className={styles.question}>
            <dt className={styles.questionTerm}>Is it worth grading this card?</dt>
            <dd className={styles.questionBody}>
              Always answerable. It weighs selling the card as it is against grading it first, and
              what you paid for it does not come into it.
            </dd>
          </div>
          <div className={styles.question}>
            <dt className={styles.questionTerm}>Did this card make money?</dt>
            <dd className={styles.questionBody}>
              Only answerable if you say what you paid. It is a different sum with a different
              answer, and the two are reported separately.
            </dd>
          </div>
        </dl>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>What you paid</h2>
        <div className={styles.field}>
          <label className={styles.label} htmlFor="acquisition-cost">
            Acquisition cost <span className={styles.optional}>(optional)</span>
          </label>
          <div className={styles.amountRow}>
            <span className={styles.currency} aria-hidden="true">
              S$
            </span>
            <input
              className={styles.input}
              id="acquisition-cost"
              name="acquisition_cost"
              type="text"
              inputMode="decimal"
              autoComplete="off"
              aria-invalid={problems.acquisition_cost !== undefined}
              aria-describedby={`acquisition-cost-hint${
                problems.acquisition_cost === undefined ? "" : " acquisition-cost-problem"
              }`}
            />
          </div>
          <p className={styles.hint} id="acquisition-cost-hint">
            Leave this blank if you do not know, or would rather not say. Blank means{" "}
            <strong>unknown</strong> — it is not the same as 0.00, and nothing here guesses it from
            the market price. Every amount on this screen is in Singapore dollars.
          </p>
          <Problem id="acquisition-cost-problem" message={problems.acquisition_cost} />
        </div>
      </section>

      <section className={styles.section}>
        <fieldset className={styles.fieldset}>
          <legend className={styles.sectionHeading}>Companies to compare</legend>
          <p className={styles.hint}>
            Each company grades on its own scale, so each one gets its own answer.
          </p>
          <div className={styles.choices}>
            {companies.map((company) => (
              <label
                className={styles.choice}
                key={company.company}
                htmlFor={`company-${company.company}`}
              >
                <input
                  className={styles.checkbox}
                  id={`company-${company.company}`}
                  name="grading_companies"
                  type="checkbox"
                  value={company.company}
                  defaultChecked
                />
                <span className={styles.choiceLabel}>{company.display_name}</span>
                <span className={styles.choiceNote}>
                  {company.grades.length} grades, {company.grades[0]} to{" "}
                  {company.grades[company.grades.length - 1]}
                </span>
              </label>
            ))}
          </div>
          <Problem id="companies-problem" message={problems.grading_companies} />
        </fieldset>
      </section>

      <section className={styles.section}>
        <fieldset className={styles.fieldset}>
          <legend className={styles.sectionHeading}>What to optimize for</legend>
          <div className={styles.choices}>
            {MODES.map((option, index) => (
              <label className={styles.choice} key={option.mode} htmlFor={`mode-${option.mode}`}>
                <input
                  className={styles.checkbox}
                  id={`mode-${option.mode}`}
                  name="optimization_mode"
                  type="radio"
                  value={option.mode}
                  defaultChecked={index === 0}
                />
                <span className={styles.choiceLabel}>{option.label}</span>
                <span className={styles.choiceNote}>{option.explanation}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </section>

      {/*
       * Collapsed, and blank inside. Six cost fields is a lot on a phone, and
       * every one of them already has a figure server-side — so the ordinary
       * path is to leave the section shut, and the exact amounts that were used
       * are shown on the next screen rather than typed in here.
       */}
      <details className={styles.costs}>
        <summary className={styles.summary}>Change the costs</summary>
        <p className={styles.hint}>
          Anything left blank uses a standard Singapore submission cost, and the next screen shows
          exactly what was used. These are separate line items and are never added into one figure.
        </p>

        <div className={styles.costFields}>
          {COST_FIELDS.map((field) => (
            <div className={styles.field} key={field.name}>
              <label className={styles.label} htmlFor={fieldId(field.name)}>
                {field.label}
              </label>
              <div className={styles.amountRow}>
                <span className={styles.currency} aria-hidden="true">
                  S$
                </span>
                <input
                  className={styles.input}
                  id={fieldId(field.name)}
                  name={field.name}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="standard"
                  aria-invalid={problems[field.name] !== undefined}
                  {...(problems[field.name] === undefined
                    ? {}
                    : { "aria-describedby": `${fieldId(field.name)}-problem` })}
                />
              </div>
              <Problem id={`${fieldId(field.name)}-problem`} message={problems[field.name]} />
            </div>
          ))}

          <div className={styles.field}>
            <label className={styles.label} htmlFor="selling-fee-rate">
              Selling fee
            </label>
            <div className={styles.amountRow}>
              <input
                className={styles.input}
                id="selling-fee-rate"
                name="selling_fee_rate"
                type="text"
                inputMode="decimal"
                autoComplete="off"
                placeholder="standard"
                aria-invalid={problems.selling_fee_rate !== undefined}
                aria-describedby={`selling-fee-hint${
                  problems.selling_fee_rate === undefined ? "" : " selling-fee-rate-problem"
                }`}
              />
              <span className={styles.currency} aria-hidden="true">
                %
              </span>
            </div>
            <p className={styles.hint} id="selling-fee-hint">
              What the marketplace takes of the sale, as a percentage. It is charged whether you
              grade the card or sell it as it is, so it sits on both sides of the comparison.
            </p>
            <Problem id="selling-fee-rate-problem" message={problems.selling_fee_rate} />
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="selling-fee-flat">
              Fixed fee per sale
            </label>
            <div className={styles.amountRow}>
              <span className={styles.currency} aria-hidden="true">
                S$
              </span>
              <input
                className={styles.input}
                id="selling-fee-flat"
                name="selling_fee_flat"
                type="text"
                inputMode="decimal"
                autoComplete="off"
                placeholder="standard"
                aria-invalid={problems.selling_fee_flat !== undefined}
                {...(problems.selling_fee_flat === undefined
                  ? {}
                  : { "aria-describedby": "selling-fee-flat-problem" })}
              />
            </div>
            <Problem id="selling-fee-flat-problem" message={problems.selling_fee_flat} />
          </div>
        </div>
      </details>

      {failure !== undefined && <SubmitFailure failure={failure} waitSeconds={waitSeconds} />}

      <div className={styles.actions}>
        {offerable && (
          <button className={styles.submit} type="submit" disabled={saving}>
            {saving ? "Recording…" : "Use these figures"}
          </button>
        )}
      </div>

      <p className={styles.footnote}>
        These are recorded once and cannot be changed afterwards — pricing the card differently is a
        new analysis.
      </p>
    </form>
  );
}

function SubmitFailure({
  failure,
  waitSeconds,
}: {
  readonly failure: ConfigureFailure;
  readonly waitSeconds: number;
}) {
  return (
    <div className={styles.failure} role="alert">
      <p className={styles.failureBody}>{failure.message}</p>
      <p className={styles.failureBody}>
        {failure.action === "wait"
          ? waitSeconds > 0
            ? `Sending is paused for ${String(waitSeconds)} more second${waitSeconds === 1 ? "" : "s"}.`
            : "You can send these figures again now."
          : failure.action === "gone"
            ? "There is nothing to send from here. Photographing the card again is what starts over."
            : "Nothing has been recorded. Sending again is safe."}
      </p>
    </div>
  );
}

/**
 * What was recorded.
 *
 * **Every figure here is read off the response**, including the ones the user
 * left blank — that is what makes the standard costs visible at all, and it is
 * how this screen shows them without `apps/web` knowing what they are.
 *
 * The five committed line items are listed one by one and **never summed**.
 * §47's later dimensions — tax, service tier, shipping provider — attach to
 * individual lines, so a total is a figure that would have to be unpicked again;
 * and the selling fee is not one of the five, because it is paid out of a sale
 * that may not happen rather than committed up front.
 */
function Recorded({
  configuration,
  companies,
}: {
  readonly configuration: EconomicConfigurationResponse;
  readonly companies: readonly GradingCompanyResponse[];
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const router = useRouter();

  // The form unmounts with the submit button, so without this a keyboard user
  // is dropped back at the top of the document with nothing announced.
  useEffect(() => {
    heading.current?.focus();
  }, []);

  // Recording the configuration completed the analysis (#244), so the results
  // exist from this moment: the next step is real, and the screen goes there.
  useEffect(() => {
    const timer = setTimeout(() => router.push("/results"), ADVANCE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [router]);

  const displayName = (slug: string) =>
    companies.find((company) => company.company === slug)?.display_name ?? slug;
  const modeLabel =
    MODES.find((option) => option.mode === configuration.optimization_mode)?.label ??
    configuration.optimization_mode;
  const money = (amount: string) => `${configuration.currency} ${amount}`;

  return (
    <div className={styles.gate} role="status">
      <h1 className={styles.heading} ref={heading} tabIndex={-1}>
        These are the figures the analysis will use.
      </h1>

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt className={styles.term}>What you paid</dt>
          <dd className={styles.value} data-supplied={configuration.acquisition_cost !== null}>
            {configuration.acquisition_cost === null
              ? "Not supplied"
              : money(configuration.acquisition_cost)}
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Companies</dt>
          <dd className={styles.value}>
            {configuration.grading_companies.map(displayName).join(", ")}
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Optimizing for</dt>
          <dd className={styles.value}>{modeLabel}</dd>
        </div>
      </dl>

      {configuration.acquisition_cost === null && (
        <p className={styles.body}>
          Because you did not say what you paid, we can tell you whether grading this card is worth
          it — but not whether buying it made money. Those are the two questions, and only the first
          of them has what it needs.
        </p>
      )}

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Costs</h2>
        <dl className={styles.facts}>
          {COST_FIELDS.map((field) => (
            <div className={styles.fact} key={field.name}>
              <dt className={styles.term}>{field.label}</dt>
              <dd className={styles.value}>{money(configuration.costs[field.name])}</dd>
            </div>
          ))}
          <div className={styles.fact}>
            <dt className={styles.term}>Selling fee</dt>
            <dd className={styles.value}>
              {proportionToPercent(configuration.costs.selling_fee.rate)}% of the sale
              {configuration.costs.selling_fee.flat === "0.00"
                ? ""
                : `, plus ${money(configuration.costs.selling_fee.flat)}`}
            </dd>
          </div>
        </dl>
      </section>

      <p className={styles.body}>
        Everything the results need is now recorded. Next is the recommendation, and what grading
        this card is expected to come to with each company.
      </p>
      <p className={styles.footnote}>
        These figures cannot be changed — photographing the card again is what starts over.
      </p>

      <div className={styles.actions}>
        <Link className={styles.submit} href="/results">
          See the results
        </Link>
      </div>

      <p className={styles.footnote}>Taking you there in a moment.</p>
    </div>
  );
}
