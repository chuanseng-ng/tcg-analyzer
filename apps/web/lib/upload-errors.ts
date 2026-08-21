/**
 * What a failure from `POST /analyses` or `POST /analyses/{id}/images` means to
 * a person holding a card and a phone.
 *
 * A sibling of `./card-errors`, deliberately not an extension of it. That
 * module's three outcomes — `missing`, `unreachable`, `unexpected` — describe
 * `GET /cards/{id}`, where every failure is about an identifier in a link. Here
 * the failures are about a photograph the user just took, and two of them
 * (throttled, and the analysis having moved on) have no counterpart there at
 * all. Merging the two would invent a meaning for both.
 *
 * Each outcome names the **one thing worth offering next**, which is the part
 * that has to be right: a 429 must not be offered a Retry button, because
 * pressing it fires straight back into the limit that produced it (ADR 0005).
 */

import { ApiError } from "./api";

/**
 * What the screen should offer.
 *
 * - `retake` — this photograph is the problem; choose another.
 * - `wait` — throttled. The copy carries the countdown and there is no button.
 * - `restart` — this analysis can no longer take photographs. Begin a new one.
 * - `retry` — nothing is wrong with the photograph; the service is not
 *   answering. Sending it again is a reasonable thing to do.
 */
export type UploadAction = "retake" | "wait" | "restart" | "retry";

export interface UploadFailure {
  /** User-facing copy. Never a developer message and never a decoder's words. */
  readonly message: string;
  readonly action: UploadAction;
  /** Present only for `wait`, and only when the service said how long. */
  readonly retryAfterSeconds?: number;
}

const UNREACHABLE =
  "The service is not answering right now. Your photograph has not been sent — try again in a moment.";

export function classifyUploadFailure(error: unknown): UploadFailure {
  if (!(error instanceof ApiError)) {
    return { message: UNREACHABLE, action: "retry" };
  }

  // The endpoint writes this message for a reader: it names the rule that was
  // broken and says nothing about how the decoder failed (#33). Showing it
  // beats paraphrasing, because this module cannot know which of the byte,
  // pixel, format and decode rules the file tripped.
  if (error.code === "invalid_image") {
    return {
      message: error.serverMessage ?? "That photograph is not one this service can read.",
      action: "retake",
    };
  }

  if (error.status === 429) {
    return {
      message: "Too many uploads from this connection.",
      action: "wait",
      ...(error.retryAfterSeconds === undefined
        ? {}
        : { retryAfterSeconds: error.retryAfterSeconds }),
    };
  }

  // The analysis has moved past the point where its images can change. Nothing
  // the user does to this photograph will help; a new analysis is the way on.
  if (error.status === 409) {
    return {
      message: "This analysis has already moved on and can no longer take photographs.",
      action: "restart",
    };
  }

  // One 404 covers an unknown analysis, someone else's, a missing cookie and a
  // lapsed one — deliberately indistinguishable (#32). To a user who was
  // uploading a moment ago, all four mean the same thing: the session is gone.
  if (error.status === 404) {
    return {
      message: "This analysis is no longer available. Sessions expire after a week.",
      action: "restart",
    };
  }

  // No status means the request never reached the service, which is the same
  // fact to a reader as the service refusing to answer.
  if (error.code === "provider_error" || error.status === undefined) {
    return { message: UNREACHABLE, action: "retry" };
  }

  return {
    message: "The service answered with something this page did not understand.",
    action: "retry",
  };
}
