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
 * **Not every 409 is a "not yet", and #36 is where that stopped being true.**
 * An analysis that has `failed` also is not at the gate, and never will be —
 * spec §65 has no edge out of it. Offering "try again" there is a loop with no
 * exit, and it was wrong for a dead-lettered job before the quality gate
 * existed. The service now says which it is with two of spec §66's codes, so
 * the distinction is read from the envelope rather than guessed from the
 * status.
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
 * - `gone` — the analysis or the session no longer exists, the card does not,
 *   or the analysis has failed for good. Confirming again cannot help; there is
 *   nothing to confirm against.
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

  // The gate refused the photographs (spec §19). Permanent: §65 has no way out
  // of `failed`, so this needs new photographs rather than another tap. What
  // was wrong with them was already said on `/analyze`, which is where the
  // retake is — and a link to it from the confirmation gate is exactly what
  // #91 refuses to open, in this branch as in every other.
  if (error.code === "image_quality_failure") {
    return {
      message:
        "These photographs could not be analysed. Start again with new ones when you are ready.",
      action: "gone",
    };
  }

  // The analysis failed for some other reason — a job that ran out of retries,
  // or a dependency that never came back. Said without blaming the photographs,
  // because nothing here suggests they were the problem.
  if (error.code === "analysis_failed") {
    return {
      message: "This analysis did not finish. Starting again is the only way on from here.",
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
  // is making is the one they can see. A `failed` analysis no longer reaches
  // here: the two branches above take it, which is what makes "try again"
  // honest again.
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
