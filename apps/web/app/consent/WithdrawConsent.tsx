"use client";

import { useEffect, useState } from "react";

import {
  getConsentText,
  withdrawTrainingConsent,
  type ConsentTextResponse,
  type WithdrawalResponse,
} from "@/lib/api";
import { classifyConsentFailure, type ConsentFailure } from "@/lib/consent-errors";

import styles from "./page.module.css";

/**
 * One question — what is your code — and one irreversible answer.
 *
 * **Nothing is read until the button is pressed.** There is no route that looks
 * a code up, deliberately: one would let somebody holding a guess learn it was
 * real without spending it. So this screen cannot show what is kept before it
 * deletes it, and it says so rather than pretending to check first.
 *
 * **The confirmation is rendered from the `DELETE`'s own body**, `ReportGrade`'s
 * rule and for its reason: the code is spent, and a second call is the same bare
 * 404 an unknown one gets. There is nothing left to re-read.
 *
 * **`kept` is not a failure.** Spec §31 makes a published dataset version an
 * immutable record of what a model was trained on, so a photograph already
 * inside one stays — which the consent text said before anybody agreed to it,
 * because we would rather somebody knew that beforehand than discovered it now.
 */
export function WithdrawConsent() {
  const [text, setText] = useState<ConsentTextResponse | null>(null);
  const [code, setCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [withdrawn, setWithdrawn] = useState<WithdrawalResponse | null>(null);
  const [failure, setFailure] = useState<ConsentFailure | null>(null);
  const [waitSeconds, setWaitSeconds] = useState(0);

  // What was agreed to, so somebody deciding whether to withdraw can read it
  // again. The current version rather than theirs: there is no way to ask which
  // version a code was minted under without a lookup route, and the words have
  // not changed if the version has not.
  useEffect(() => {
    const controller = new AbortController();
    getConsentText(controller.signal)
      .then(setText)
      .catch(() => setText(null));
    return () => controller.abort();
  }, []);

  // `CardUpload`'s countdown: the limiter answers with `Retry-After` and a
  // button that fired straight back into it would be worse than a wait (ADR
  // 0005).
  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds((left) => left - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  async function withdraw() {
    if (saving || waitSeconds > 0 || code.trim() === "") return;

    setSaving(true);
    setFailure(null);
    try {
      setWithdrawn(await withdrawTrainingConsent(code.trim()));
    } catch (error: unknown) {
      const classified = classifyConsentFailure(error);
      setFailure(classified);
      if (classified.retryAfterSeconds !== undefined) {
        setWaitSeconds(classified.retryAfterSeconds);
      }
    } finally {
      setSaving(false);
    }
  }

  if (withdrawn !== null) {
    return <Withdrawn result={withdrawn} />;
  }

  return (
    <div className={styles.screen}>
      <h1 className={styles.heading}>Withdraw a photograph you let us keep</h1>
      <p className={styles.body}>
        If you said yes when you uploaded a card, you were shown a code. Enter it here and every
        photograph it covers is deleted — the file and the record of it together.
      </p>
      <p className={styles.footnote}>
        We cannot look a code up for you, and nothing on this page checks one before it is used:
        being able to check would let somebody find out whether a guess was real. So there is one
        button, and it does the whole thing.
      </p>

      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          void withdraw();
        }}
      >
        <div className={styles.field}>
          <label className={styles.label} htmlFor="withdrawal-code">
            Your code
          </label>
          <input
            className={styles.control}
            id="withdrawal-code"
            name="withdrawal-code"
            type="text"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="A3KDM-9F2QT-BXWR7-N0HJ5"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <p className={styles.footnote}>
            Upper and lower case are the same, and the dashes are optional.
          </p>
        </div>

        <div className={styles.actions}>
          <button
            className={styles.action}
            type="submit"
            disabled={saving || waitSeconds > 0 || code.trim() === ""}
          >
            {saving ? "Withdrawing…" : "Withdraw"}
          </button>
        </div>
      </form>

      {failure !== null &&
        (failure.action === "gone" ? (
          <Gone />
        ) : (
          <p className={styles.failure} role="alert">
            {failure.message}
            {waitSeconds > 0 &&
              ` Try again in ${waitSeconds} second${waitSeconds === 1 ? "" : "s"}.`}
          </p>
        ))}

      {text !== null && (
        <section className={styles.agreed} aria-labelledby="agreed">
          <h2 className={styles.sectionHeading} id="agreed">
            What you agreed to
          </h2>
          {text.paragraphs.map((paragraph) => (
            <p className={styles.footnote} key={paragraph}>
              {paragraph}
            </p>
          ))}
        </section>
      )}
    </div>
  );
}

/**
 * The three reasons a code stops working, none of them claimed.
 *
 * The service answers unknown, mistyped and already-withdrawn with one bare 404
 * and tells them apart nowhere, so this page names all three. A page that could
 * say *which* would tell a guesser their code was real — the same argument
 * `/feedback/[code]`'s four-reason screen makes, one reason shorter because a
 * consent has no expiry: a photograph is kept until somebody takes it back.
 */
function Gone() {
  return (
    <div className={styles.failure} role="alert">
      <p className={styles.body}>No photographs are kept under that code.</p>
      <p className={styles.body}>A code stops working for one of three reasons:</p>
      <ul className={styles.reasons}>
        <li>it was already used — withdrawing is the one thing a code does;</li>
        <li>it was mistyped;</li>
        <li>it was never one of ours.</li>
      </ul>
      <p className={styles.footnote}>
        There is no way to look one up. The code is all we keep of the consent, and we keep only its
        fingerprint — deliberately, because anything that could find your photographs from a code
        could find them from a guess.
      </p>
    </div>
  );
}

function Withdrawn({ result }: { readonly result: WithdrawalResponse }) {
  return (
    <div className={styles.screen}>
      <h1 className={styles.heading}>
        {result.deleted === 0
          ? "Nothing was left to delete."
          : `${result.deleted} ${result.deleted === 1 ? "photograph is" : "photographs are"} gone.`}
      </h1>
      {result.deleted > 0 && (
        <p className={styles.body}>
          The {result.deleted === 1 ? "file and its record are" : "files and their records are"}{" "}
          deleted. Your code is spent, and there is nothing left for it to address.
        </p>
      )}
      {result.kept > 0 && (
        <p className={styles.body}>
          {result.kept === 1 ? "One photograph stays" : `${result.kept} photographs stay`}, because{" "}
          {result.kept === 1 ? "it is" : "they are"} already inside a published training set. A
          published set is a fixed record of what a model learned from, so changing one would make a
          past result impossible to reproduce. This is what the consent said before you agreed to
          it, and it is why we would rather you knew then than found out now.
        </p>
      )}
    </div>
  );
}
