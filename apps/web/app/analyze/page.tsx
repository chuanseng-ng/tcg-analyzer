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
 * This is NOT the upload flow. Image capture, the quality gate and card
 * confirmation are M2 (#15 onward); the results UI is M9.
 */
export default function AnalyzePage() {
  return (
    <main>
      <Container>
        <div className={styles.page}>
          <Stack gap={4}>
            <h1 className={styles.heading}>Analysis is not built yet</h1>
            <p className={styles.body}>
              Uploading a card&apos;s front and back, checking the photographs are usable and
              confirming which card it is arrive in M2. Nothing on this page accepts an image yet.
            </p>
            <Link className={styles.back} href="/">
              Back to the start
            </Link>
          </Stack>
        </div>
      </Container>
    </main>
  );
}
