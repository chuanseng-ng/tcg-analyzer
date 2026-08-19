"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { ApiError, searchCards, type CardSearchResponse } from "@/lib/api";
import {
  PAGE_SIZE,
  cardsHref,
  hasFilters,
  pageRange,
  parseSearchQuery,
  toApiParams,
  type CardSearchQuery,
} from "@/lib/card-search";

import { CardResults } from "./CardResults";
import styles from "./CardSearch.module.css";

/**
 * How a failed search is described to the user.
 *
 * The server's own message is deliberately not echoed. It is safe to show, but
 * it is written for an API consumer; this page needs to say what the user can
 * do next, and only the page knows that.
 */
type Failure = "unreachable" | "unexpected";

type SearchState =
  | { readonly status: "searching" }
  | { readonly status: "found"; readonly page: CardSearchResponse }
  | { readonly status: "failed"; readonly failure: Failure };

/**
 * A transport failure and a `provider_error` are the same fact to a user: the
 * catalog is not answering right now, and trying again later is the move. They
 * are separated from everything else because everything else is a bug rather
 * than an outage, and telling someone to retry a bug wastes their time.
 */
function classify(error: unknown): Failure {
  if (
    error instanceof ApiError &&
    (error.code === "provider_error" || error.status === undefined)
  ) {
    return "unreachable";
  }
  return "unexpected";
}

/**
 * The search form, its results, and the paging between them.
 *
 * The URL is the state. Submitting the form navigates rather than setting
 * component state, so a search survives a reload, answers the back button, and
 * can be sent to somebody else — and the confirmation gate's "Change" action
 * (#91) has a plain link to come back to.
 *
 * The form is uncontrolled and read from `FormData` on submit. That is the
 * shape the decision to require an explicit submit implies: Japanese input goes
 * through IME composition, and a per-keystroke listener fires on half-composed
 * kana, issuing requests for text the user has not finished typing.
 */
export function CardSearch() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // `searchParams` is a new object every render; its serialization is not, and
  // it is what every downstream memo and effect should actually depend on.
  const paramsKey = searchParams.toString();
  const query = useMemo(() => parseSearchQuery(new URLSearchParams(paramsKey)), [paramsKey]);

  const [state, setState] = useState<SearchState>({ status: "searching" });
  // Bumped by Retry. A failed search has no other reason to re-run: the query
  // has not changed, so nothing else in the effect's dependencies has either.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setState({ status: "searching" });

    searchCards(toApiParams(query), controller.signal)
      .then((page) => {
        if (active) setState({ status: "found", page });
      })
      .catch((error: unknown) => {
        // An abort is this component unmounting or superseding its own request,
        // not a failure the user should be told about.
        if (active && !controller.signal.aborted) {
          setState({ status: "failed", failure: classify(error) });
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [query, attempt]);

  const submit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const field = (name: string) => String(form.get(name) ?? "").trim();

      // A new search always returns to the first page. Keeping the old offset
      // would answer a different question with page three of it.
      router.push(
        cardsHref({
          text: field("text"),
          cardNumber: field("card_number"),
          setCode: field("set_code"),
          language: field("language"),
          offset: 0,
        }),
      );
    },
    [router],
  );

  const narrowed = hasFilters(query);

  return (
    <div className={styles.search}>
      {/* Remounted whenever the URL changes, so the fields show the search that
          is actually on screen — including after "Clear filters". */}
      <form key={paramsKey} className={styles.form} role="search" onSubmit={submit}>
        <div className={styles.primary}>
          <label className={styles.label} htmlFor="card-text">
            Card name
          </label>
          <div className={styles.primaryRow}>
            <input
              className={styles.input}
              id="card-text"
              name="text"
              type="search"
              autoComplete="off"
              defaultValue={query.text}
              placeholder="Charizard, リザードン…"
            />
            <button className={styles.submit} type="submit">
              Search
            </button>
          </div>
          <p className={styles.hint}>
            Any part of the printed name, in either alphabet. Leave it empty to browse everything.
          </p>
        </div>

        <details className={styles.narrow} open={narrowed}>
          <summary className={styles.summary}>Narrow this search</summary>
          <div className={styles.narrowFields}>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="card-number">
                Card number
              </label>
              <input
                className={styles.input}
                id="card-number"
                name="card_number"
                type="text"
                inputMode="numeric"
                autoComplete="off"
                defaultValue={query.cardNumber}
                placeholder="25"
              />
              <p className={styles.hint}>
                Matched from the start, so <code>25</code> finds <code>025/165</code>.
              </p>
            </div>

            <div className={styles.field}>
              <label className={styles.label} htmlFor="card-set">
                Set code
              </label>
              <input
                className={styles.input}
                id="card-set"
                name="set_code"
                type="text"
                autoComplete="off"
                defaultValue={query.setCode}
                placeholder="BS"
              />
              <p className={styles.hint}>As printed — BS, MEW, SV2a.</p>
            </div>

            <div className={styles.field}>
              <label className={styles.label} htmlFor="card-language">
                Language
              </label>
              <select
                className={styles.input}
                id="card-language"
                name="language"
                defaultValue={query.language}
              >
                <option value="">Any language</option>
                <option value="en">English</option>
                <option value="ja">Japanese</option>
              </select>
            </div>
          </div>
        </details>
      </form>

      <div className={styles.results} aria-busy={state.status === "searching"}>
        <Outcome
          state={state}
          query={query}
          narrowed={narrowed}
          onRetry={() => setAttempt((previous) => previous + 1)}
        />
      </div>
    </div>
  );
}

interface OutcomeProps {
  readonly state: SearchState;
  readonly query: CardSearchQuery;
  readonly narrowed: boolean;
  readonly onRetry: () => void;
}

function Outcome({ state, query, narrowed, onRetry }: OutcomeProps) {
  if (state.status === "searching") {
    return (
      <p className={styles.status} role="status" aria-live="polite">
        Searching the catalog…
      </p>
    );
  }

  if (state.status === "failed") {
    return (
      <div className={styles.failure} role="alert">
        <p className={styles.failureHeading}>
          {state.failure === "unreachable"
            ? "The card catalog is unavailable right now."
            : "That search could not be completed."}
        </p>
        <p className={styles.failureBody}>
          {state.failure === "unreachable"
            ? "Nothing is wrong with your search — the catalog service is not answering. Try again in a moment."
            : "The catalog answered with something this page did not understand. Trying again may help."}
        </p>
        <button className={styles.retry} type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }

  const { page } = state;

  // An empty page is a truthful answer, never a 404 and never an error: the
  // catalog is reachable and nothing in it matches. An offset past the end
  // lands here too, which is why "clear the filters" is offered rather than a
  // claim that the card does not exist.
  if (page.cards.length === 0) {
    return (
      <div className={styles.empty}>
        <p className={styles.emptyHeading}>
          {narrowed || page.offset > 0
            ? "No cards match this search."
            : "The catalog has no cards in it yet."}
        </p>
        {narrowed || page.offset > 0 ? (
          <>
            <p className={styles.emptyBody}>
              Card numbers match from the start, so a shorter one finds more. Names match any part
              of what is printed.
            </p>
            <Link className={styles.clear} href="/cards">
              Clear the filters
            </Link>
          </>
        ) : null}
      </div>
    );
  }

  const { first, last } = pageRange(page.offset, page.cards.length);

  return (
    <>
      <p className={styles.count} role="status" aria-live="polite">
        Showing {first}–{last} of {page.total}
      </p>
      <CardResults cards={page.cards} />
      <Paging query={query} page={page} />
    </>
  );
}

function Paging({
  query,
  page,
}: {
  readonly query: CardSearchQuery;
  readonly page: CardSearchResponse;
}) {
  const hasPrevious = page.offset > 0;
  const hasNext = page.offset + page.cards.length < page.total;

  if (!hasPrevious && !hasNext) {
    return null;
  }

  const previousOffset = Math.max(page.offset - PAGE_SIZE, 0);
  const nextOffset = page.offset + page.cards.length;

  return (
    <nav className={styles.paging} aria-label="Result pages">
      {hasPrevious ? (
        <Link className={styles.page} href={cardsHref({ ...query, offset: previousOffset })}>
          Previous
        </Link>
      ) : (
        <span className={styles.pageDisabled} aria-hidden="true">
          Previous
        </span>
      )}
      {hasNext ? (
        <Link className={styles.page} href={cardsHref({ ...query, offset: nextOffset })}>
          Next
        </Link>
      ) : (
        <span className={styles.pageDisabled} aria-hidden="true">
          Next
        </span>
      )}
    </nav>
  );
}
