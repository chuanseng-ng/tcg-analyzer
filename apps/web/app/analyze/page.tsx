import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";
import { Stack } from "@/components/Stack";

import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Analyze a card",
  description: "The upload and analysis flow. Arriving in M2 — this page is a placeholder.",
};

/**
 * Placeholder so the landing call to action is not a dead button.
 *
 * This is NOT the upload flow. Image capture and the quality gate are M2
 * (#15 onward); the results UI is M9. Card confirmation is no longer among the
 * missing pieces — it landed with #91 and lives at `/identify` — but it is
 * deliberately not linked from here: in M1 a candidate arrives by manual
 * selection from the catalog, and nothing on this page can produce one.
 */
export default function AnalyzePage() {
  return (
    <main>
      <Container>
        <div className={styles.page}>
          <Stack gap={4}>
            <h1 className={styles.heading}>Analysis is not built yet</h1>
            <p className={styles.body}>
              Uploading a card&apos;s front and back and checking the photographs are usable arrive
              in M2. Nothing on this page accepts an image yet.
            </p>
            <p className={styles.body}>
              Confirming which card you are holding is already built. Find your card in the catalog
              and it will ask you to confirm it before anything else happens.
            </p>
            <div className={styles.actions}>
              <Link className={styles.cta} href="/cards">
                Find your card in the catalog
              </Link>
              <Link className={styles.back} href="/">
                Back to the start
              </Link>
            </div>
          </Stack>
        </div>
      </Container>
    </main>
  );
}
