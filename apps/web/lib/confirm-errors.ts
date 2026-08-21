/**
 * What a failure from `POST /analyses/{id}/confirm-card` means to a person who
 * has just said "yes, this is my card".
 *
 * A third sibling of `./card-errors` and `./upload-errors`, and not an
 * extension of either, on the reasoning both of those record: the outcomes
 * differ. A 409 here is **not** the upload's "this analysis has moved on and
 * needs replacing" — it is "the analysis is not at the confirmation gate yet",
 * which is what a worker that has not caught up looks like, and the right thing
 * to offer is the same tap again. Merging the two would give one status two
 * meanings.
 *
 * Nothing here offers a way into `/analyze`. The confirmation gate has no route
 * onward to analysis in either direction (#91), and a failure is not the place
 * to open one.
 */

import { ApiError } from "./api";

/**
 * What the screen should offer.
 *
 * - `retry` — try the same confirmation again. Covers the service not
 *   answering, and the analysis not having reached the gate yet.
 * - `wait` — throttled. The copy carries the countdown and there is no button,
 *   because pressing one fires straight back into the limit (ADR 0005).
 * - `gone` — the analysis or the session no longer exists, or the card does
 *   not. Confirming again cannot help; there is nothing to confirm against.
 */
export type ConfirmAction = "retry" | "wait" | "gone";

export interface ConfirmFailure {
  /** User-facing copy. Never a developer message. */
  readonly message: string;
  readonly action: ConfirmAction;
  /** Present only for `wait`, and only when the service said how long. */
  readonly retryAfterSeconds?: number;
}

const UNREACHABLE =
  "The service is not answering right now. Nothing has been recorded — try again in a moment.";

export function classifyConfirmFailure(error: unknown): ConfirmFailure {
  if (!(error instanceof ApiError)) {
    return { message: UNREACHABLE, action: "retry" };
  }

  // The identifier in the link names no card the catalog holds. Said in the
  // same words `card-errors.ts` gives the same fact, because it is the same
  // fact — the server resolved this identifier and found nothing.
  if (error.code === "card_not_identified") {
    return {
      message: "No card is recorded under that identifier.",
      action: "gone",
    };
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

  // The analysis is not at the confirmation gate. Either your photographs are
  // still being prepared, or this analysis was confirmed already — and the
  // second is not something to say out loud, because the confirmation the user
  // is making is the one they can see.
  if (error.status === 409) {
    return {
      message: "Your photographs are not ready for this yet.",
      action: "retry",
    };
  }

  // One 404 covers an unknown analysis, someone else's, a missing cookie and a
  // lapsed one — deliberately indistinguishable (#32).
  if (error.status === 404) {
    return {
      message: "The analysis these photographs belong to is no longer available.",
      action: "gone",
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
