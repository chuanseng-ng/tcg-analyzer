import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { Container } from "@/components/Container";

import { CardConfirmation } from "./CardConfirmation";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Confirm your card",
  description:
    "Confirm which card you are holding before anything is analysed. " +
    "Set, number, variant and rarity are what you check against.",
};

/**
 * The identification-confirmation gate (spec §20, §48).
 *
 * Spec §20 states the rule this screen exists to enforce — the user must
 * confirm the result, and an uncertain identification is never used silently
 * for economic analysis. It is a product-integrity gate rather than a
 * convenience screen, which is why it has a route of its own instead of a
 * button somewhere in the catalog.
 *
 * This page is a shell; {@link CardConfirmation} holds the gate itself, because
 * the candidate arrives as `?card_id=` and reading it is a client concern. The
 * `Suspense` boundary below is load-bearing rather than decorative — a client
 * component calling `useSearchParams` with no boundary above it fails
 * `next build` during prerender, and the error names prerendering rather than
 * Suspense. Do not remove it.
 */
export default function IdentifyPage() {
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
            <Suspense fallback={<p className={styles.pending}>Preparing the confirmation…</p>}>
              <CardConfirmation />
            </Suspense>
          </div>
        </Container>
      </main>
    </>
  );
}
