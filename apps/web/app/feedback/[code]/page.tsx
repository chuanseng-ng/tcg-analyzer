import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import styles from "./page.module.css";
import { ReportGrade } from "./ReportGrade";

export const metadata: Metadata = {
  title: "Report the grade",
  description:
    "What this card was predicted to grade, and the form for saying what it actually received.",
};

/**
 * Spec §68's way back — issue #274, over #270's return code.
 *
 * The code is in the path because there is nothing else to address the row
 * with: weeks later the `tcg_session` cookie has expired, the tab is closed,
 * and §53 forbids the account that would otherwise carry the identity. It is a
 * bearer capability, which is why the row it addresses holds no photograph, no
 * session and no address — there is nothing behind it to expose.
 *
 * A static title, `/cards/[cardId]`'s rule: everything is fetched in the
 * browser because `NEXT_PUBLIC_API_BASE_URL` is the API as the *browser*
 * reaches it, and naming the card here would need a second base URL. It also
 * keeps the code out of anything rendered on a server.
 */
export default async function FeedbackPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;

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
            <ReportGrade code={code} />
          </div>
        </Container>
      </main>
    </>
  );
}
