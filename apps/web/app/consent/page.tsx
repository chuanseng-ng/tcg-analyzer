import type { Metadata } from "next";
import Link from "next/link";

import { Container } from "@/components/Container";

import styles from "./page.module.css";
import { WithdrawConsent } from "./WithdrawConsent";

export const metadata: Metadata = {
  title: "Withdraw a photograph",
  description:
    "What you agreed to when you let us keep a photograph, and how to take it back with the code you were given.",
};

/**
 * The way back from ADR 0008's approved class 4 — issue #148.
 *
 * **The code is typed rather than carried in the path**, which is where this
 * screen departs from `/feedback/[code]`. That one is reached with a code a user
 * already has in front of them; this one is reached weeks later by somebody who
 * has changed their mind, and a bearer capability in a URL is a bearer
 * capability in browser history, in a referrer, and in whatever the address bar
 * autocompletes for the next person on the machine. There is nothing to gain by
 * putting it there: the page reads nothing until the button is pressed.
 *
 * No session is read and none is needed. Spec §54 has deleted the session that
 * produced the photographs — deliberately, so a per-browser identifier is not
 * kept forever — and §53 forbids the account that would replace it.
 */
export default function ConsentPage() {
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
            <WithdrawConsent />
          </div>
        </Container>
      </main>
    </>
  );
}
