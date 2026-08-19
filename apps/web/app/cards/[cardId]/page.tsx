import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import { CardDetail } from "./CardDetail";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Card detail",
  description:
    "Everything the canonical catalog records about one card: set, number, " +
    "variant, rarity and provider identifiers.",
};

/**
 * The canonical record for one card.
 *
 * The title is static rather than the card's name: the card is fetched in the
 * browser, for the same reason the search is — `NEXT_PUBLIC_API_BASE_URL` is
 * the API as the *browser* reaches it, and a server render inside the Compose
 * network cannot use it. Naming the card in `generateMetadata` would mean a
 * second base URL, a Compose change and a second way for the two to disagree.
 */
export default async function CardPage({ params }: { params: Promise<{ cardId: string }> }) {
  const { cardId } = await params;

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
            <CardDetail cardId={cardId} />
          </div>
        </Container>
      </main>
    </>
  );
}
