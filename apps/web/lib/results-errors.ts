/**
 * What a failure while reading an analysis's results means to a person who has
 * come to see them.
 *
 * A fifth sibling of `./card-errors`, `./upload-errors`, `./confirm-errors` and
 * `./economics-errors`, and not an extension of any of them, for the reason each
 * records: one status means different things on different routes. Here there is
 * no 409 and no 429 — nothing is written and the reads are not rate-limited — so
 * the whole question is whether reading again could ever help.
 *
 * Both reads the results screen makes are classified here: the poll on
 * `GET /analyses/{id}` and the one `GET /analyses/{id}/results` that follows it.
 * They answer the same bare 404 and the same 503 `provider_error`, so one
 * classifier is honest for both.
 */

import { ApiError } from "./api";

/**
 * What the screen should offer.
 *
 * - `restart` — the analysis is gone: unknown, someone else's, or a session that
 *   has lapsed (#32). Reading again cannot bring it back; photographing the card
 *   again is what starts over.
 * - `retry` — a store would not answer, or the request never left. Nothing has
 *   been lost, so the same read a moment later is the right move.
 * - `unexpected` — the service answered something this page cannot read. A
 *   retry is not offered because nothing suggests it would come right.
 */
export type ResultsAction = "restart" | "retry" | "unexpected";

export interface ResultsFailure {
  /** User-facing copy. Never a developer message. */
  readonly message: string;
  readonly action: ResultsAction;
}

const UNREACHABLE =
  "The service is not answering right now. Nothing has been lost — try again in a moment.";

export function classifyResultsFailure(error: unknown): ResultsFailure {
  if (!(error instanceof ApiError)) {
    return { message: UNREACHABLE, action: "retry" };
  }

  // One 404 covers an unknown analysis, someone else's, a missing cookie and a
  // lapsed one — deliberately indistinguishable (#32), and none comes back.
  if (error.status === 404) {
    return {
      message:
        "The analysis these results belong to is no longer available. Photographing the card again is what starts over.",
      action: "restart",
    };
  }

  // The four stores the results route reads from all answer 503
  // `provider_error`; which one is in `details.reason`, for an operator.
  if (error.code === "provider_error" || error.status === undefined) {
    return { message: UNREACHABLE, action: "retry" };
  }

  return {
    message: "The service answered with something this page did not understand.",
    action: "unexpected",
  };
}
