import Link from "next/link";

import type { CardSummaryResponse } from "@/lib/api";
import { cardHref, languageLabel, rarityLabel, variantLabel } from "@/lib/card-display";

import styles from "./CardResults.module.css";

/**
 * One page of search results.
 *
 * Every row states its variant. Holo, reverse holo and 1st edition are
 * economically different cards, and there is no artwork to tell them apart by
 * (ADR 0004), so a row that showed only a name and a number would let someone
 * open the wrong Charizard and get a valuation for a card they do not own.
 * The whole row is the link, and its accessible name carries all of it, so the
 * distinction survives into a screen reader's list of links.
 *
 * Pure and presentational on purpose: it takes rows and renders them, which is
 * what makes "the variants are distinguishable" a cheap assertion to write.
 */
export function CardResults({ cards }: { readonly cards: readonly CardSummaryResponse[] }) {
  return (
    <ul className={styles.results}>
      {cards.map((card) => (
        <li className={styles.item} key={card.id}>
          <Link className={styles.link} href={cardHref(card.id)}>
            <span className={styles.name}>{card.name}</span>
            <span className={styles.provenance}>
              {card.set.name} · {card.set.set_code} {card.card_number}
            </span>
            <span className={styles.tags}>
              <span className={styles.variant} data-recorded={card.variant !== null}>
                {variantLabel(card.variant)}
              </span>
              <span className={styles.tag}>{rarityLabel(card.rarity)}</span>
              <span className={styles.tag}>{languageLabel(card.language)}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
