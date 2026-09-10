/**
 * What a failure from the training-consent endpoints means to a person.
 *
 * A seventh sibling of `./card-errors`, `./upload-errors`, `./confirm-errors`,
 * `./economics-errors`, `./results-errors` and `./feedback-errors`, and not an
 * extension of any of them, for the reason each of those records: the outcomes
 * differ, and one status means different things on different routes.
 *
 * **A failure to consent must never cost the user their analysis.** Every action
 * here is something a screen says beside a flow that carries on regardless —
 * ADR 0008's consent is optional, and a page that blocked on it would have made
 * declining expensive after all.
 *
 * **The 404 is the whole taxonomy in one status.** `tcg_api.routers.consent`
 * answers unknown, mistyped and already-withdrawn identically and on purpose —
 * a well-formed guess must learn nothing a malformed one would not, and there is
 * no read route to check a code with first — so this classifier cannot tell them
 * apart either, and the copy must name the reasons rather than pick one.
 *
 * **The 409 is photographs that are already kept, not a retry.** The code was
 * shown once and no row can produce it again, so it is a terminal fact and
 * offering a button would be a loop with no exit.
 */

import { ApiError } from "./api";

/**
 * What the screen should offer.
 *
 * - `retry` — send it again. The service did not answer, or answered something
 *   this page could not read.
 * - `wait` — throttled. The copy carries the countdown and there is no button,
 *   because pressing one fires straight back into the limit (ADR 0005).
 * - `gone` — the code addresses nothing, and no amount of asking will change
 *   that: unknown, mistyped, or already withdrawn.
 * - `kept` — these photographs are already in the corpus. A code was shown once
 *   for them and cannot be shown again.
 */
export type ConsentAction = "retry" | "wait" | "gone" | "kept";

export interface ConsentFailure {
  /** User-facing copy. Never a developer message. */
  readonly message: string;
  readonly action: ConsentAction;
  /** Present only for `wait`, and only when the service said how long. */
  readonly retryAfterSeconds?: number;
}

const UNREACHABLE =
  "The service is not answering right now. Nothing has been kept — try again in a moment.";

export function classifyConsentFailure(error: unknown): ConsentFailure {
  if (!(error instanceof ApiError)) {
    return { message: UNREACHABLE, action: "retry" };
  }

  if (error.status === 429) {
    return {
      message: "Too many requests from this connection.",
      action: "wait",
      ...(error.retryAfterSeconds === undefined
        ? {}
        : { retryAfterSeconds: error.retryAfterSeconds }),
    };
  }

  // One 404 for three different facts, deliberately indistinguishable. The
  // withdrawal screen lists the reasons rather than claiming one of them.
  if (error.status === 404) {
    return {
      message: "No photographs are kept under that code.",
      action: "gone",
    };
  }

  // Consenting only. The analysis stored no photograph, or these photographs
  // are already in the corpus — and the second is the one worth saying, because
  // the first cannot happen on a screen that has just uploaded two.
  if (error.status === 409) {
    return {
      message: "These photographs are already kept, and the code for them was shown once.",
      action: "kept",
    };
  }

  if (error.code === "provider_error" || error.status === undefined) {
    return { message: UNREACHABLE, action: "retry" };
  }

  return {
    message: "The service answered with something this page did not understand.",
    action: "retry",
  };
}
