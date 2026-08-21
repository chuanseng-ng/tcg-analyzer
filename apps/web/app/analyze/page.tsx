import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import { CardUpload } from "./CardUpload";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Photograph your card",
  description:
    "Photograph the front and back of your card. Ordinary photographs in ordinary light — " +
    "no lightbox and no tripod.",
};

/**
 * The upload screen — spec §48, issue #34. The landing page's call to action
 * has always pointed here.
 *
 * A shell only; {@link CardUpload} holds the screen, because staging files,
 * previewing them and sending them are all client concerns. No `Suspense`
 * boundary is needed — nothing here reads the search parameters — which is the
 * one thing that differs from `/identify`'s otherwise identical shape.
 *
 * This page is where the pipeline begins and it deliberately leads nowhere
 * afterwards. Confirming which card the photographs show writes to the analysis
 * (#104), and running the analysis needs that confirmation first.
 */
export default function AnalyzePage() {
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
          <CardUpload />
        </Container>
      </main>
    </>
  );
}
