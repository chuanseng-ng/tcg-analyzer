import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import { EconomicConfiguration } from "./EconomicConfiguration";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Price the decision",
  description:
    "Say what grading this card would cost you, and what you paid for it if you know. " +
    "Whether grading is worth it and whether the card made money are two different questions.",
};

/**
 * The configuration screen — spec §48, §45, §46, §43, issue #66.
 *
 * A shell only; {@link EconomicConfiguration} holds the form, because the
 * analysis it writes to comes out of `sessionStorage` and the companies come out
 * of `GET /grading-companies`, both of which are browser concerns. No `Suspense`
 * boundary is needed — nothing here reads the search parameters — which is the
 * one thing that differs from `/identify`'s otherwise identical shape.
 *
 * This is the step after the confirmation gate: spec §5 puts the economics
 * behind a confirmed card, and `POST /analyses/{id}/economic-configuration`
 * accepts them only while the analysis is `analyzing`, which is the state
 * confirming the card reaches — and recording them is what completes the
 * analysis (#244), so the recorded view leads on to `/results`.
 */
export default function ConfigurePage() {
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
            <EconomicConfiguration />
          </div>
        </Container>
      </main>
    </>
  );
}
