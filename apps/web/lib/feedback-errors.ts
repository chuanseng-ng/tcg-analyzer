/**
 * What a failure from the feedback endpoints means to a person holding a code.
 *
 * A fifth sibling of `./card-errors`, `./upload-errors`, `./confirm-errors` and
 * `./economics-errors`, and not an extension of any of them, for the reason each
 * of those records: the outcomes differ, and one status means different things
 * on different routes.
 *
 * **The 404 here is the whole taxonomy in one status.** `tcg_api.routers.feedback`
 * answers unknown, expired, already-answered and not-a-code-at-all identically
 * and on purpose — a well-formed guess must learn nothing a malformed one would
 * not — so this classifier cannot tell them apart either, and the copy that
 * follows must name the reasons a code stops working rather than pick one.
 *
 * **The 409 is a mint that already happened, not a retry.** The code was shown
 * once and the row cannot produce it again, so "already issued" is a terminal
 * fact and offering a button would be a loop with no exit — `economics-errors`'
 * correction, carried forward.
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
 *   that: unknown, expired, or already answered.
 * - `issued` — a code was already minted for this analysis. It was shown once
 *   and cannot be shown again.
 */
export type FeedbackAction = "retry" | "wait" | "gone" | "issued";

export interface FeedbackFailure {
  /** User-facing copy. Never a developer message. */
  readonly message: string;
  readonly action: FeedbackAction;
  /** Present only for `wait`, and only when the service said how long. */
  readonly retryAfterSeconds?: number;
}

const UNREACHABLE =
  "The service is not answering right now. Nothing has been recorded — try again in a moment.";

export function classifyFeedbackFailure(error: unknown): FeedbackFailure {
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

  // One 404 for four different facts, deliberately indistinguishable. The
  // screens list the reasons rather than claiming one of them.
  if (error.status === 404) {
    return {
      message: "No feedback is recorded under that code.",
      action: "gone",
    };
  }

  // Minting only. The analysis is not finished, it stored no prediction, or a
  // code has already been handed out for it — and the third is the one worth
  // saying, because the other two cannot happen on a screen showing results.
  if (error.status === 409) {
    return {
      message: "A code was already issued for this analysis, and it was shown once.",
      action: "issued",
    };
  }

  // FastAPI's own validation refusal. The form offers only grades the chosen
  // company issues, so reaching here means the service refused something the
  // browser thought was fine.
  if (error.status === 422) {
    return {
      message: "The service would not accept that grade. Check the company and send it again.",
      action: "retry",
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
