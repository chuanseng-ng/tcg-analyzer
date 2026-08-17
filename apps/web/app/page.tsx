import Link from "next/link";

import { ApiStatus } from "@/components/ApiStatus";
import { Container } from "@/components/Container";
import { Stack } from "@/components/Stack";

import styles from "./page.module.css";

export default function LandingPage() {
  return (
    <>
      <header>
        <Container>
          <p className={styles.brand}>TCG Grading Advisor</p>
        </Container>
      </header>

      <main>
        <Container>
          <div className={styles.page}>
            <Stack gap={5}>
              <h1 className={styles.heading}>Should you grade this card?</h1>
              {/* spec §48 — secondary explanation, verbatim. */}
              <p className={styles.lead}>
                Estimate condition, likely grades and whether grading is worthwhile.
              </p>
              {/* spec §48 — primary call to action. */}
              <Link className={styles.cta} href="/analyze">
                Analyze a card
              </Link>
              <ApiStatus />
            </Stack>

            <Stack gap={4}>
              <p className={styles.body}>
                Photograph the front and back of an ungraded Pokémon card. You get an
                identification, a read on its physical condition, likely grades for PSA, TAG and
                BGS, and the numbers behind whether sending it in pays for itself.
              </p>
              <ul className={styles.scopeList}>
                <li>Identify the card, and confirm it with you before going on.</li>
                <li>Read centering, corners, edges and surface from the photos.</li>
                <li>Show how each company is likely to grade it, side by side.</li>
                <li>Weigh the fees, the wait and the resale spread, in SGD.</li>
              </ul>
            </Stack>

            <p className={styles.disclaimer}>
              TCG Grading Advisor is not an official grading service and does not authenticate
              cards. Every grade it reports is a probability, never a guaranteed grade, and where
              the photographs cannot support a confident answer it says so instead of inventing one.
            </p>
          </div>
        </Container>
      </main>

      <footer>
        <Container>
          <p className={styles.footer}>
            V1 covers Pokémon cards in English and Japanese, with costs in SGD.
          </p>
        </Container>
      </footer>
    </>
  );
}
