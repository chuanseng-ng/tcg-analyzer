import Link from "next/link";

import { Container } from "@/components/Container";

import styles from "./recovery.module.css";

/**
 * A whole screen with one way back — what `error.tsx`, `global-error.tsx`
 * and `not-found.tsx` each render (#261).
 *
 * The same shell every route draws for itself: the brand, a `<main>`, one
 * heading, one paragraph, one link. No hooks and no data, so it renders on
 * either side of the client boundary and needs nothing from the API — a
 * page that exists because something else failed cannot depend on the thing
 * that failed. What it is told to say is all it says: nothing here reads an
 * error object.
 */
export function Recovery({
  heading,
  body,
  href,
  action,
}: {
  readonly heading: string;
  readonly body: string;
  readonly href: "/" | "/analyze";
  readonly action: string;
}) {
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
            <div className={styles.gate}>
              <h1 className={styles.heading}>{heading}</h1>
              <p className={styles.body}>{body}</p>
              <div className={styles.actions}>
                <Link className={styles.action} href={href}>
                  {action}
                </Link>
              </div>
            </div>
          </div>
        </Container>
      </main>
    </>
  );
}

/**
 * The crash copy, shared by `error.tsx` and `global-error.tsx` so both
 * boundaries say the same. The way out is the start rather than `/analyze`:
 * a link straight back into the screen that just threw would loop.
 */
export function Crashed() {
  return (
    <Recovery
      heading="This page could not be shown."
      body={
        "Something went wrong before it could be drawn, and nothing here suggests you were " +
        "the cause. The start is the surest way back."
      }
      href="/"
      action="Back to the start"
    />
  );
}
