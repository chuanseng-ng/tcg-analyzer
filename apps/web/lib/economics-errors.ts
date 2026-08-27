/**
 * What a failure from the economics endpoints means to a person who has just
 * priced their own decision.
 *
 * A fourth sibling of `./card-errors`, `./upload-errors` and `./confirm-errors`,
 * and not an extension of any of them, for the reason each of those records: the
 * outcomes differ, and one status means different things on different routes.
 *
 * **The 409 here is terminal, where `confirm-errors`' is a "not yet".** This
 * screen is only reachable once `POST /analyses/{id}/confirm-card` has
 * succeeded, and that call is what moves the analysis to `analyzing` — the one
 * state the configuration is accepted in (spec §5). So a refusal here is not a
 * worker that has yet to catch up; it is a configuration already recorded, or an
 * analysis that has moved past the step. Neither is fixed by tapping again, and
 * #65 makes it once-and-for-all: the configuration is immutable and pricing the
 * card differently is a new analysis. Offering "try again" would be a loop with
 * no exit, which is the mistake `confirm-errors` had to correct for `failed`.
 *
 * FastAPI answers 404, 409 and 422 here with its own `{"detail": …}` rather than
 * the spec §66 envelope — the taxonomy has no code for a malformed request and
 * #65 declined to invent a ninth — so these branch on the status. The 503 does
 * carry the envelope, and is read from `code` like everywhere else.
 */

import { ApiError } from "./api";

/**
 * What the screen should offer.
 *
 * - `retry` — send the same configuration again. The service did not answer, or
 *   refused something in the form.
 * - `wait` — throttled. The copy carries the countdown and there is no button,
 *   because pressing one fires straight back into the limit (ADR 0005).
 * - `gone` — there is nothing left to configure: the analysis or session has
 *   expired, or the economics are already recorded. Sending again cannot help.
 */
export type ConfigureAction = "retry" | "wait" | "gone";

export interface ConfigureFailure {
  /** User-facing copy. Never a developer message. */
  readonly message: string;
  readonly action: ConfigureAction;
  /** Present only for `wait`, and only when the service said how long. */
  readonly retryAfterSeconds?: number;
}

const UNREACHABLE =
  "The service is not answering right now. Nothing has been recorded — try again in a moment.";

export function classifyConfigureFailure(error: unknown): ConfigureFailure {
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

  // One 404 covers an unknown analysis, someone else's, a missing cookie and a
  // lapsed one — deliberately indistinguishable (#32).
  if (error.status === 404) {
    return {
      message: "The analysis these photographs belong to is no longer available.",
      action: "gone",
    };
  }

  // Already recorded, or past the step. The two are not told apart because the
  // service does not distinguish them in a body this client can read, and the
  // answer is the same either way.
  if (error.status === 409) {
    return {
      message: "These economics are already recorded, and they cannot be changed.",
      action: "gone",
    };
  }

  // FastAPI's own validation refusal. Everything this form can produce is
  // checked beside the field first, so reaching here means the service refused
  // something the browser thought was fine.
  if (error.status === 422) {
    return {
      message: "The service would not accept these figures. Check the amounts and send them again.",
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

/**
 * Why the grading companies could not be listed.
 *
 * Two branches rather than `card-errors`' three, because `GET
 * /grading-companies` has no "not found": the only documented failure is 503
 * `provider_error` with `grading_rules_unreachable`. Without the list there is no
 * form — the companies, their display names and their scales all come from that
 * response and none of them is hard-coded here.
 */
export type CompaniesFailure = "unreachable" | "unexpected";

export function classifyCompaniesFailure(error: unknown): CompaniesFailure {
  if (!(error instanceof ApiError)) return "unreachable";
  return error.code === "provider_error" || error.status === undefined
    ? "unreachable"
    : "unexpected";
}
