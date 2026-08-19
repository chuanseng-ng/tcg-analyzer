"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { getCard, type CardResponse } from "@/lib/api";
import { cardHref, languageLabel, rarityLabel, variantLabel } from "@/lib/card-display";
import { classifyCardFailure, type CardFailure } from "@/lib/card-errors";
import { certaintyOf, manuallySelected, type ConfirmationCandidate } from "@/lib/identification";

import styles from "./page.module.css";

type ConfirmationState =
  | { readonly status: "nothing_selected" }
  | { readonly status: "loading" }
  | { readonly status: "awaiting"; readonly candidate: ConfirmationCandidate }
  | { readonly status: "confirmed"; readonly candidate: ConfirmationCandidate }
  | { readonly status: "failed"; readonly failure: CardFailure };

/**
 * Ask the user whether this is the card in their hand, and take their answer.
 *
 * Two properties matter more than any of the copy, and both are structural:
 *
 * **There is no auto-confirm, at any confidence.** The only transition into
 * `confirmed` is the click handler, written as a functional update that refuses
 * to fire from any state but `awaiting`. Spec §20 requires the user to confirm;
 * a 99% match still takes a tap.
 *
 * **The screen never presents itself as settled.** The heading is a question,
 * the certainty is stated before the card rather than after it, and Change
 * carries the same weight as Confirm whenever nothing has actually identified
 * the card — which in M1 is always, because no producer of a
 * `CardIdentification` exists until M2's image pipeline.
 *
 * There is deliberately no `useRouter` here. Change is a plain link, so nothing
 * in this component can navigate on its own.
 */
export function CardConfirmation() {
  const searchParams = useSearchParams();
  // Blank is absent, the same rule `card-search.ts` applies to every filter.
  const cardId = (searchParams.get("card_id") ?? "").trim();

  const [state, setState] = useState<ConfirmationState>(
    cardId === "" ? { status: "nothing_selected" } : { status: "loading" },
  );
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (cardId === "") {
      setState({ status: "nothing_selected" });
      return;
    }

    const controller = new AbortController();
    let active = true;

    // A changed card_id re-enters loading, which is what drops a confirmation
    // that belonged to a different card.
    setState({ status: "loading" });

    getCard(cardId, controller.signal)
      .then((card) => {
        if (active) setState({ status: "awaiting", candidate: manuallySelected(card) });
      })
      .catch((error: unknown) => {
        if (active && !controller.signal.aborted) {
          setState({ status: "failed", failure: classifyCardFailure(error) });
        }
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [cardId, attempt]);

  const confirm = useCallback(() => {
    setState((current) =>
      current.status === "awaiting"
        ? { status: "confirmed", candidate: current.candidate }
        : current,
    );
  }, []);

  if (state.status === "nothing_selected") {
    return <NothingSelected />;
  }

  if (state.status === "loading") {
    return (
      <p className={styles.status} role="status" aria-live="polite">
        Looking this card up…
      </p>
    );
  }

  if (state.status === "failed") {
    return (
      <Failed failure={state.failure} onRetry={() => setAttempt((previous) => previous + 1)} />
    );
  }

  if (state.status === "confirmed") {
    return <Confirmed candidate={state.candidate} />;
  }

  return <Awaiting candidate={state.candidate} onConfirm={confirm} />;
}

/**
 * Arriving with no candidate.
 *
 * Not an error and not `role="alert"`: nothing has gone wrong, there is simply
 * nothing to confirm yet, and in M1 that is the ordinary state of the pipeline.
 */
function NothingSelected() {
  return (
    <div className={styles.gate}>
      <h1 className={styles.heading}>No card is selected.</h1>
      <p className={styles.body}>
        This screen confirms a card you have already found in the catalog. Nothing has analysed a
        photograph — uploading them arrives in M2 — so there is no detected card to show you here
        yet.
      </p>
      <div className={styles.actions}>
        <Link className={styles.confirm} href="/cards">
          Find a card
        </Link>
      </div>
    </div>
  );
}

function Failed({
  failure,
  onRetry,
}: {
  readonly failure: CardFailure;
  readonly onRetry: () => void;
}) {
  if (failure === "missing") {
    return (
      <div className={styles.failure} role="alert">
        <h1 className={styles.failureHeading}>No card is recorded under that identifier.</h1>
        <p className={styles.failureBody}>
          The identifier may have been mistyped, or the link may be from an older version of the
          catalog. Searching for the card by name will find it if it is here.
        </p>
        <Link className={styles.change} href="/cards">
          Back to the search
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.failure} role="alert">
      <h1 className={styles.failureHeading}>
        {failure === "unreachable"
          ? "The card catalog is unavailable right now."
          : "This card could not be loaded."}
      </h1>
      <p className={styles.failureBody}>
        {failure === "unreachable"
          ? "The catalog service is not answering. Nothing is wrong with the link — try again in a moment."
          : "The catalog answered with something this page did not understand."}
      </p>
      <div className={styles.failureActions}>
        <button className={styles.retry} type="button" onClick={onRetry}>
          Try again
        </button>
        <Link className={styles.change} href="/cards">
          Back to the search
        </Link>
      </div>
    </div>
  );
}

function Awaiting({
  candidate,
  onConfirm,
}: {
  readonly candidate: ConfirmationCandidate;
  readonly onConfirm: () => void;
}) {
  const certainty = certaintyOf(candidate);

  return (
    <div className={styles.gate}>
      {/* A question, never an assertion. This screen does not know the answer. */}
      <h1 className={styles.heading}>Is this the card you are holding?</h1>

      <section className={styles.certainty} data-certainty={certainty.state}>
        <p className={styles.certaintyLine}>
          <span className={styles.certaintyTerm}>Identification confidence</span>
          <span className={styles.certaintyValue}>{certainty.label}</span>
        </p>
        <p className={styles.certaintyFraming}>{certainty.framing}</p>
      </section>

      <CardIdentity card={candidate.card} />

      {/*
       * DOM order is fixed — Confirm, then Change — so keyboard and screen
       * reader order never shifts with the certainty. `data-certainty` drives
       * emphasis instead: with nothing measured, Change carries the same visual
       * weight as Confirm, so the interface leads with search rather than
       * presenting a guess as the answer.
       */}
      <div className={styles.actions} data-certainty={certainty.state}>
        <button className={styles.confirm} type="button" onClick={onConfirm}>
          Confirm this card
        </button>
        <Link className={styles.change} href="/cards">
          Change card
        </Link>
      </div>

      <p className={styles.footnote}>
        Confirming records nothing and analyses nothing. It only says that this is the card you
        mean.
      </p>
    </div>
  );
}

function Confirmed({ candidate }: { readonly candidate: ConfirmationCandidate }) {
  const heading = useRef<HTMLHeadingElement>(null);

  // The Confirm button unmounts with the gate, so without this a keyboard user
  // is dropped back at the top of the document with nothing announced.
  useEffect(() => {
    heading.current?.focus();
  }, []);

  return (
    <div className={styles.gate} role="status">
      <h1 className={styles.heading} ref={heading} tabIndex={-1}>
        Confirmed: this is the card you are holding.
      </h1>

      <CardIdentity card={candidate.card} />

      <p className={styles.body}>
        Nothing has been analysed. Uploading photographs of this card, reading its condition, the
        likely grades from PSA, TAG and BGS, and the economics of sending it in all arrive in M2.
      </p>
      <p className={styles.footnote}>
        This confirmation lives on this page only. It is not saved, and reloading or closing the tab
        forgets it.
      </p>

      <div className={styles.actions}>
        <Link className={styles.change} href="/cards">
          Start over with another card
        </Link>
      </div>
    </div>
  );
}

/**
 * The card, as something to hold up against the one in the user's hand.
 *
 * ADR 0004 imports no catalog artwork, so set, number, variant and rarity carry
 * the comparison and get the weight a picture would have had. `metadata` and the
 * provider identifiers are deliberately absent: they are catalog bookkeeping
 * rather than something a person checks against a card, and the full record is
 * one link away for anyone who wants it.
 */
function CardIdentity({ card }: { readonly card: CardResponse }) {
  return (
    <article className={styles.identity}>
      <div className={styles.headline}>
        <h2 className={styles.name}>{card.name}</h2>
        <p className={styles.subtitle}>
          {card.set.name} · {card.set.set_code} {card.card_number}
        </p>
      </div>

      {/* Not a broken image: this product imports no catalog artwork at all. */}
      <div className={styles.placeholder} role="presentation">
        <p className={styles.placeholderLabel}>No card image</p>
      </div>
      <p className={styles.placeholderNote}>
        This catalog carries no card artwork, so the facts below are what you check against — not a
        picture.
      </p>

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt className={styles.term}>Set</dt>
          <dd className={styles.value}>
            {card.set.name} ({card.set.set_code})
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Card number</dt>
          <dd className={styles.value}>{card.card_number}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Variant</dt>
          <dd className={styles.value} data-recorded={card.variant !== null}>
            {variantLabel(card.variant)}
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Rarity</dt>
          <dd className={styles.value}>{rarityLabel(card.rarity)}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Language</dt>
          <dd className={styles.value}>{languageLabel(card.language)}</dd>
        </div>
      </dl>

      <Link className={styles.record} href={cardHref(card.id)}>
        See this card&apos;s full catalog record
      </Link>
    </article>
  );
}
