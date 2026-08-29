import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { Container } from "@/components/Container";

import { WorkList } from "./WorkList";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Images awaiting annotation",
  description: "Training images that carry no defect marker and no centering measurement.",
};

/**
 * The work list — which images are waiting, and a way into one.
 *
 * A shell; {@link WorkList} holds the list itself, because paging lives in the
 * URL and reading it is a client concern. The `Suspense` boundary is
 * load-bearing rather than decorative: a client component calling
 * `useSearchParams` with no boundary above it fails `next build` during
 * prerender. `apps/web/app/cards/page.tsx` carries the same note.
 */
export default function WorkListPage() {
  return (
    <>
      <header>
        <Container>
          <p className={styles.brand}>
            <Link className={styles.brandLink} href="/">
              Annotation — internal tool
            </Link>
          </p>
        </Container>
      </header>

      <main>
        <Container>
          <div className={styles.page}>
            <Suspense fallback={<p className={styles.pending}>Reading the corpus…</p>}>
              <WorkList />
            </Suspense>
          </div>
        </Container>
      </main>
    </>
  );
}
