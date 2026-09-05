/**
 * Spec §65's nine states, as the one place this app spells them.
 *
 * `AnalysisResponse.status` is a bare `string` in the generated schema — the
 * service keeps it open so a tenth state costs no schema change — so the names
 * are written down here rather than read off the types. Every screen that
 * branches on a state imports from this module; none carries a literal of its
 * own.
 *
 * `queued` is not here on purpose: it is the transport word
 * `POST /analyses/{id}/run` answers with, and no row ever holds it (#35).
 * `calculating` is listed because §65 lists it, and is never observed (#244).
 */

export type AnalysisStatus =
  | "created"
  | "uploading"
  | "uploaded"
  | "identifying"
  | "awaiting_confirmation"
  | "analyzing"
  | "calculating"
  | "completed"
  | "failed";

/** In the order the specification lists them, which is the happy path. */
export const ANALYSIS_STATUSES: readonly AnalysisStatus[] = [
  "created",
  "uploading",
  "uploaded",
  "identifying",
  "awaiting_confirmation",
  "analyzing",
  "calculating",
  "completed",
  "failed",
];

/**
 * Whether the analysis will move again. §65 has no edge out of either terminal
 * state, so a poll stops here and nowhere else.
 */
export function isTerminal(status: string): boolean {
  return status === "completed" || status === "failed";
}

/**
 * Not finished. A state this app has never heard of is read as still working:
 * the alternative is a client claiming a result exists because a word it did
 * not recognise arrived.
 */
export function isWorking(status: string): boolean {
  return !isTerminal(status);
}

export function isFailed(status: string): status is "failed" {
  return status === "failed";
}

/**
 * What the analysis is doing right now, for a screen that is waiting on it.
 *
 * One line per state, written for the person waiting rather than as the
 * state's name. `analyzing` reads as waiting for the costs because, after #227,
 * that is what it is: the grades were predicted at the worker's claim and the
 * configuration is the last input the results need (#244).
 */
const STEP_COPY: Readonly<Record<AnalysisStatus, string>> = {
  created: "Waiting for the photographs.",
  uploading: "Storing the photographs.",
  uploaded: "Waiting for the analysis to start.",
  identifying: "Checking the photographs and reading the card.",
  awaiting_confirmation: "Waiting for you to confirm which card this is.",
  analyzing: "Waiting for the costs to be set.",
  calculating: "Working out the economics.",
  completed: "Putting the results together.",
  failed: "This analysis did not finish.",
};

export function stepCopy(status: string): string {
  return (STEP_COPY as Readonly<Record<string, string>>)[status] ?? `Still working (${status}).`;
}
