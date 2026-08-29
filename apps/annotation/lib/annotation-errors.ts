/**
 * What a failure from the internal annotation surface means to an annotator.
 *
 * `apps/web`'s `lib/card-errors.ts` in shape and in reasoning: three outcomes,
 * branched on the spec §66 code rather than on the status, because a route may
 * override the taxonomy's default status and the code is what does not move.
 *
 * The three are chosen by what the reader can *do*, which is why there are three
 * and not five. `retry` is offered only where retrying could work — retrying a
 * bug wastes the time of somebody with several hundred cards to get through.
 */

import { ApiError } from "./api";

export type AnnotationFailure = "missing" | "refused" | "unreachable" | "unexpected";

export function classifyAnnotationFailure(error: unknown): AnnotationFailure {
  if (!(error instanceof ApiError)) {
    return "unexpected";
  }

  // The internal surface answers a bare 404 rather than a §66 envelope, on
  // `GET /analyses/{id}`'s reasoning: none of the eight codes means "not found".
  // So this is the one branch here that reads a status.
  if (error.status === 404) {
    return "missing";
  }

  // A malformed identifier is FastAPI's own request-validation 422, which
  // carries no §66 code. To a reader it is the same fact as an unknown one —
  // the link does not lead to an image — and it is emphatically not worth a
  // retry, because a bad identifier fails identically for ever.
  if (error.status === 422 && error.code === undefined) {
    return "missing";
  }

  // No status means the request never reached the service, which is the same
  // fact to a reader as the corpus refusing to answer.
  if (error.code === "provider_error" || error.status === undefined) {
    return "unreachable";
  }

  return "unexpected";
}

/**
 * What to tell the annotator. Copy lives here rather than in each screen so the
 * work list and the viewer cannot describe one failure two ways.
 */
export const FAILURE_MESSAGE: Record<AnnotationFailure, string> = {
  missing: "That image is not in the corpus.",
  refused:
    "The corpus would not take that annotation. Check the severity, the confidence and whether the image has an artifact to place coordinates against.",
  unreachable: "The corpus is not answering right now.",
  unexpected: "Something went wrong reading the corpus.",
};

/** Whether offering a retry could plausibly help. */
export function isWorthRetrying(failure: AnnotationFailure): boolean {
  return failure === "unreachable";
}

/**
 * The same three outcomes, read for a *save* rather than a read.
 *
 * `classifyAnnotationFailure` maps a bare 422 to `missing`, and that is right
 * where the only thing a 422 can be is a malformed identifier in the URL — the
 * link does not lead to an image. **On a write it is wrong**: a 422 is this
 * endpoint refusing the body, and a 409 is it refusing coordinates for an image
 * with no artifact. Telling an annotator their image is not in the corpus when
 * they have just forgotten a severity is the sort of message that makes somebody
 * stop trusting a tool.
 *
 * Neither is worth a retry, which is why `refused` is a fourth outcome rather
 * than being folded into `unexpected`: the annotator has to change something, and
 * the copy says what.
 */
export function classifySaveFailure(error: unknown): AnnotationFailure {
  if (error instanceof ApiError && (error.status === 409 || error.status === 422)) {
    return "refused";
  }

  return classifyAnnotationFailure(error);
}
