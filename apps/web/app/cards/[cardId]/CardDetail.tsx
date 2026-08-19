"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getCard, type CardResponse } from "@/lib/api";
import {
  languageLabel,
  metadataValueLabel,
  rarityLabel,
  releaseDateLabel,
  variantLabel,
} from "@/lib/card-display";
import { classifyCardFailure, type CardFailure } from "@/lib/card-errors";
import { identifyHref } from "@/lib/identification";

import styles from "./page.module.css";

type DetailState =
  | { readonly status: "loading" }
  | { readonly status: "loaded"; readonly card: CardResponse }
  | { readonly status: "failed"; readonly failure: CardFailure };

/**
 * Everything the catalog records about one card.
 *
 * Text-forward by necessity: ADR 0004 imports no artwork, so set, number,
 * variant and rarity carry the disambiguation a picture would have done. There
 * is a placeholder where the image would be rather than a broken `<img>` — the
 * absence is a decision, and it is stated as one.
 *
 * There is exactly one way forward from here, and it goes *into* the
 * confirmation gate (#91) rather than around it. Confirming that this is the
 * card in the user's hand is a product-integrity gate with its own screen; this
 * page may link to that screen and to nothing beyond it, and in particular
 * never to analysis.
 */
export function CardDetail({ cardId }: { readonly cardId: string }) {
  const [state, setState] = useState<DetailState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setState({ status: "loading" });

    getCard(cardId, controller.signal)
      .then((card) => {
        if (active) setState({ status: "loaded", card });
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

  return <Loaded card={state.card} />;
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
        <Link className={styles.back} href="/cards">
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
        <Link className={styles.back} href="/cards">
          Back to the search
        </Link>
      </div>
    </div>
  );
}

function Loaded({ card }: { readonly card: CardResponse }) {
  const metadata = Object.entries(card.metadata);

  return (
    <article className={styles.card}>
      <Link className={styles.back} href="/cards">
        Back to the search
      </Link>

      <div className={styles.headline}>
        <h1 className={styles.name}>{card.name}</h1>
        <p className={styles.subtitle}>
          {card.set.name} · {card.set.set_code} {card.card_number}
        </p>
      </div>

      {/* Not a broken image: this product imports no catalog artwork at all. */}
      <div className={styles.placeholder} role="presentation">
        <p className={styles.placeholderLabel}>No card image</p>
      </div>
      <p className={styles.placeholderNote}>
        This catalog carries no card artwork. Its licence covers the card data, not the pictures, so
        the only card images this product ever shows are the photographs you upload yourself.
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
        <div className={styles.fact}>
          <dt className={styles.term}>Game</dt>
          <dd className={styles.value}>{card.game}</dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.term}>Set released</dt>
          <dd className={styles.value}>{releaseDateLabel(card.set.release_date)}</dd>
        </div>
      </dl>

      {/*
       * The single forward path out of browsing, placed where it is because the
       * facts above are what the decision rests on — metadata and provider
       * identifiers below are reference material, not part of the comparison.
       *
       * A link rather than a button: the card id fully determines the
       * destination, so it survives without JavaScript, middle-clicks, and can
       * be shared. It leads to the gate, never past it.
       */}
      <div className={styles.forward}>
        <Link className={styles.cta} href={identifyHref(card.id)}>
          This is my card
        </Link>
        <p className={styles.forwardNote}>
          Takes you to the confirmation step. Nothing is analysed and nothing is saved yet.
        </p>
      </div>

      {metadata.length > 0 ? (
        <section className={styles.section}>
          <h2 className={styles.sectionHeading}>Catalog metadata</h2>
          <dl className={styles.facts}>
            {metadata.map(([key, value]) => (
              <div className={styles.fact} key={key}>
                <dt className={styles.term}>{key}</dt>
                <dd className={styles.value}>{metadataValueLabel(value)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>Provider identifiers</h2>
        {card.external_ids.length === 0 ? (
          <p className={styles.note}>No external provider records this card.</p>
        ) : (
          // Never deduplicated: the index behind these is deliberately not
          // unique, because a provider that does not split printing variants
          // issues one identifier for several cards here.
          <ul className={styles.providers}>
            {card.external_ids.map((external) => (
              <li className={styles.provider} key={`${external.provider}:${external.external_id}`}>
                <span className={styles.providerName}>{external.provider}</span>
                <code className={styles.providerId}>{external.external_id}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className={styles.disclaimer}>
        This is the catalog&apos;s record of the card, not a valuation and not a grade. Prices and
        grading arrive later in the product.
      </p>
    </article>
  );
}
