import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import styles from "./page.module.css";
import { Results } from "./Results";

export const metadata: Metadata = {
  title: "The results",
  description:
    "Whether grading this card is recommended, and what grading it is expected to come to " +
    "with each company — with every reason in words.",
};

/**
 * The results screen — spec §49, §44, §41, issue #246.
 *
 * A shell only; {@link Results} does the work, because the analysis it reads
 * comes out of `sessionStorage` and the figures come off `GET
 * /analyses/{id}/results`, both browser concerns. No `Suspense` boundary —
 * nothing here reads the search parameters — the same shape as `/configure`.
 *
 * This is the last step: recording the configuration completed the analysis
 * (#244), so everything the results need exists by the time anyone arrives
 * here. What this screen renders is spec §49's first two priorities, the
 * recommendation and the expected economic outcome; the grade distribution
 * (#247), the company comparison (#248) and the condition (#249) have their
 * places held below them.
 */
export default function ResultsPage() {
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
            <Results />
          </div>
        </Container>
      </main>
    </>
  );
}
