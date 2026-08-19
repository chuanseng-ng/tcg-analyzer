import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { Container } from "@/components/Container";
import { Stack } from "@/components/Stack";

import { CardSearch } from "./CardSearch";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Find a card",
  description:
    "Search the canonical Pokémon card catalog by name, card number or set. " +
    "English and Japanese cards, with printing variants distinguished.",
};

/**
 * The catalog browse surface.
 *
 * This page is a shell; {@link CardSearch} holds the search itself, because the
 * query lives in the URL and reading it is a client concern. The `Suspense`
 * boundary below is load-bearing rather than decorative — a client component
 * calling `useSearchParams` with no boundary above it fails `next build` during
 * prerender. Do not remove it.
 *
 * Nothing here leads to analysis. Confirming which card the user is holding is
 * a product-integrity gate with its own screen (#91); browsing the catalog must
 * not become a way around it.
 */
export default function CardsPage() {
  return (
    <>
      <header>
        <Container>
          <p className={styles.brand}>
            <Link className={styles.brandLink} href="/">
              TCG Grading Advisor
            </Link>
          </p>
        </Container>
      </header>

      <main>
        <Container>
          <div className={styles.page}>
            <Stack gap={3}>
              <h1 className={styles.heading}>Find a card</h1>
              <p className={styles.lead}>
                Search by what is printed on the card — its name, its number, or the set it came
                from. English and Japanese cards are both in here.
              </p>
            </Stack>

            <Suspense fallback={<p className={styles.pending}>Preparing the search…</p>}>
              <CardSearch />
            </Suspense>
          </div>
        </Container>
      </main>
    </>
  );
}
