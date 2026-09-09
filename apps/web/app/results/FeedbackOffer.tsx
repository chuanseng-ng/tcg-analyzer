import { useCallback, useEffect, useState } from "react";

import { mintFeedback, type ReturnCodeResponse } from "@/lib/api";
import { classifyFeedbackFailure, type FeedbackFailure } from "@/lib/feedback-errors";

import styles from "./page.module.css";

/**
 * Spec §68's offer, on the screen that made the prediction — issue #274.
 *
 * The product predicts a grade and then never finds out. §68 is how it does:
 * the user comes back weeks later with a slab in hand and says what the card
 * actually got. What makes that possible without an account (§53, and V1's
 * exclusion list) is the return code #270 mints — a bearer capability over a
 * copy of the prediction, carrying no image, no session and no address.
 *
 * **The code is shown once and lives in component state only.** Not
 * `sessionStorage`, not `localStorage`, not a URL: the row stores a sha256 and
 * cannot reproduce it, so anywhere this page put it would be a second copy the
 * service could not revoke. Leaving the screen loses it, which is the honest
 * shape of a mechanism whose privacy comes from having nothing to look up.
 *
 * **A second tap is a 409, not a second code.** `analyses.feedback_minted_at` is
 * the once-per-analysis rule and the double-tap race guard at the same time, so
 * a reload lands here with the button live and the refusal is what says the code
 * is already out there. That is a fact to state, never a button to press again.
 */
export function FeedbackOffer({ analysisId }: { readonly analysisId: string }) {
  const [minted, setMinted] = useState<ReturnCodeResponse | null>(null);
  const [minting, setMinting] = useState(false);
  const [failure, setFailure] = useState<FeedbackFailure | null>(null);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [copied, setCopied] = useState(false);

  // Counted down rather than offered a button that would fire straight back
  // into the limit (ADR 0005) — `/configure`'s rule.
  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds((left) => left - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  const mint = useCallback(() => {
    if (minting || waitSeconds > 0) return;
    setMinting(true);
    setFailure(null);
    mintFeedback(analysisId)
      .then((response) => {
        setMinted(response);
        setMinting(false);
      })
      .catch((error: unknown) => {
        const classified = classifyFeedbackFailure(error);
        if (classified.retryAfterSeconds !== undefined)
          setWaitSeconds(classified.retryAfterSeconds);
        setFailure(classified);
        setMinting(false);
      });
  }, [analysisId, minting, waitSeconds]);

  const copy = useCallback(() => {
    if (minted === null) return;
    // A clipboard a browser refuses — an insecure origin, a denied permission —
    // is not worth an error: the code is on screen and can be written down,
    // which is what the copy beside it tells the user to do anyway.
    void navigator.clipboard?.writeText(minted.return_code).then(
      () => setCopied(true),
      () => undefined,
    );
  }, [minted]);

  if (minted !== null) {
    return (
      <div className={styles.question}>
        <h3 className={styles.questionHeading}>Write this code down.</h3>
        <p className={styles.body}>
          It is the only way back to this prediction, and it will not be shown again — not on this
          page, not by us. Nothing else is kept: no photograph, no email, no account.
        </p>
        <p className={styles.code} data-return-code>
          {minted.return_code}
        </p>
        <div className={styles.actions}>
          <button className={styles.retry} type="button" onClick={copy}>
            Copy the code
          </button>
          {copied && (
            <span className={styles.footnote} role="status">
              Copied.
            </span>
          )}
        </div>
        <p className={styles.footnote}>
          Come back to <strong>/feedback</strong> with it once the card is graded. It stops working
          on {expiryDate(minted.expires_at)}.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.question}>
      <h3 className={styles.questionHeading}>Tell us the grade this card gets.</h3>
      <p className={styles.body}>
        Everything above is a prediction. If you send this card off, you can come back and say what
        it actually received — it is how the predictions get better, and it is the only thing this
        page ever asks of you.
      </p>
      {failure === null || failure.action === "retry" ? (
        <div className={styles.actions}>
          <button className={styles.retry} type="button" onClick={mint} disabled={minting}>
            {minting ? "Getting a code…" : "Give me a code"}
          </button>
        </div>
      ) : null}
      {failure !== null && (
        <p className={styles.footnote} role="alert">
          {failure.action === "wait" && waitSeconds > 0
            ? `${failure.message} Try again in ${String(waitSeconds)}s.`
            : failure.message}
        </p>
      )}
    </div>
  );
}

/**
 * The day the code stops addressing anything, in the user's locale.
 *
 * A date rather than a duration: "180 days" is a number nobody converts, and
 * the row is swept on a clock the user cannot see.
 */
function expiryDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}
