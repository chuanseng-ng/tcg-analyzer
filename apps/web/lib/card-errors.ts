/**
 * What a failure from `GET /cards/{id}` means to a reader.
 *
 * Shared by the card detail view and the confirmation gate, because both call
 * the same endpoint and therefore face the same spec §66 taxonomy — this is a
 * fact about the API, not about a screen. It also has to be shared: "no card is
 * recorded under that identifier" must mean the same thing at the gate as it
 * does on the detail page, or the gate accepts an identifier the catalog
 * rejects.
 *
 * `CardSearch`'s two-outcome classifier is deliberately *not* folded in here.
 * It covers a different endpoint, one with no `missing` case at all — an empty
 * search is a result, never a 404 — and merging them would invent a third
 * meaning for both.
 */

import { ApiError } from "./api";

/**
 * `card_not_identified` is the spec §66 code for an identifier naming no card;
 * `provider_error` is the catalog being unreachable. The route overrides the
 * taxonomy's default status for both, so the code is what to branch on.
 */
export type CardFailure = "missing" | "unreachable" | "unexpected";

export function classifyCardFailure(error: unknown): CardFailure {
  if (!(error instanceof ApiError)) {
    return "unexpected";
  }
  if (error.code === "card_not_identified") {
    return "missing";
  }
  // A malformed identifier is FastAPI's own request-validation 422, which
  // carries no §66 code because `errors.py` deliberately leaves transport-level
  // failures alone. To a reader it is the same situation as an unknown one —
  // the link does not lead to a card — and it is emphatically not worth a
  // Retry button, because retrying a bad identifier fails identically forever.
  if (error.status === 422 && error.code === undefined) {
    return "missing";
  }
  // No status means the request never reached the server, which is the same
  // fact to a reader as the catalog refusing to answer.
  if (error.code === "provider_error" || error.status === undefined) {
    return "unreachable";
  }
  return "unexpected";
}
