import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import { ImageViewer } from "./ImageViewer";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Annotate an image",
  description: "One training image, front and back, at a magnification that shows a corner.",
};

/**
 * The viewer.
 *
 * A shell; {@link ImageViewer} holds the reading and the transform. No
 * `Suspense` boundary here — nothing reads search params, and `apps/web`'s
 * `/configure` records the same distinction for the same reason.
 *
 * No annotation controls: marking corners, edges, surface and centering is the
 * next issue. This one ends at the annotator being able to see the card
 * properly, which is the thing every one of those controls is bounded by.
 */
export default async function ImagePage({ params }: { params: Promise<{ imageId: string }> }) {
  const { imageId } = await params;

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
            <ImageViewer imageId={imageId} />
          </div>
        </Container>
      </main>
    </>
  );
}
