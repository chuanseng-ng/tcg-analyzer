/**
 * What the identification pipeline concluded, and how the gate says it out loud.
 *
 * This is a client-side model of a domain concept, **not** a wire type. There is
 * no identification endpoint to generate from — `lib/api-types.ts` is produced
 * from the OpenAPI schema, and `CardResponse` deliberately omits confidence
 * because confidence belongs to an analysis rather than to a catalog record. Do
 * not move these types there. When M2 adds an identification endpoint, its
 * generated response is *mapped into* these types rather than merged with them.
 *
 * Nothing here touches React or `fetch`, for the same reason `card-search.ts`
 * does not: the awkward part is the wording, and wording is testable on its own.
 */

import type { CardResponse } from "./api";

declare const CONFIDENCE: unique symbol;

/**
 * `tcg_domain.confidence.Confidence` — a validated number in `[0, 1]`.
 *
 * Branded, so a raw float cannot reach the identified branch without passing
 * through {@link confidence}. That is the same property the domain enforces by
 * rejecting a bare `0.82` in `CardIdentification.__post_init__`: an unvalidated
 * confidence makes "never silently use an uncertain identification" easy to
 * violate by accident.
 */
export type Confidence = number & { readonly [CONFIDENCE]: true };

/** Build a {@link Confidence}. Mirrors the domain's `InvalidConfidence`. */
export function confidence(value: number): Confidence {
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new RangeError(`confidence must be a finite number in [0, 1], got ${value}`);
  }
  return value as Confidence;
}

/**
 * `Uncertain[CardIdentification]` from `packages/domain`, exactly.
 *
 * Either a card with a measured confidence, **or** a standalone
 * insufficient-information result that carries no card at all. "A
 * `CardIdentification` with `InsufficientInformation` confidence" is not
 * representable in the domain, and it must not become representable here: the
 * domain models failing to identify a card as an *answer* returned instead of
 * an identification, not as an identification with a missing number.
 */
export type IdentificationOutcome =
  | { readonly kind: "identified"; readonly card: CardResponse; readonly confidence: Confidence }
  | { readonly kind: "insufficient_information"; readonly reason: string | null };

/** Why a card is on the confirmation screen at all. */
export type CandidateOrigin = "manual_selection" | "image_identification";

/**
 * What the confirmation screen is about.
 *
 * The card sits on the candidate rather than only inside the outcome because in
 * M1 there is no identification: the user picked the card themselves, and the
 * identifier's answer is "insufficient information", which carries no card. The
 * domain is right that those are two different things, and this keeps them
 * different rather than faking a confidence to hold them together.
 */
export interface ConfirmationCandidate {
  readonly card: CardResponse;
  readonly origin: CandidateOrigin;
  readonly outcome: IdentificationOutcome;
}

/**
 * M1's only producer: the user found the card themselves and nothing has run.
 *
 * This is the honest state, not a placeholder. No model has looked at a
 * photograph, so the pipeline has concluded nothing — and saying so is what
 * spec §2.7 requires of a system that must never fabricate certainty.
 */
export function manuallySelected(card: CardResponse): ConfirmationCandidate {
  return {
    card,
    origin: "manual_selection",
    outcome: { kind: "insufficient_information", reason: "no_identification_has_run" },
  };
}

/**
 * M2's seam: a `CardIdentification` from the image pipeline, unchanged in shape.
 *
 * M2 adds a producer, not a screen. Nothing in the product calls this yet, and
 * nothing may fabricate a candidate to reach it — a mocked detection would ship
 * a state no production path arrives at.
 */
export function identifiedFromImage(card: CardResponse, score: Confidence): ConfirmationCandidate {
  return {
    card,
    origin: "image_identification",
    outcome: { kind: "identified", card, confidence: score },
  };
}

/** Whether an identification was measured at all, as a styling hook. */
export type CertaintyState = "unknown" | "measured";

/**
 * How the gate states what it knows.
 *
 * A number alone is not an answer a non-expert reads correctly, so a label never
 * travels without its framing sentence.
 */
export interface Certainty {
  readonly state: CertaintyState;
  /** Shown against "Identification confidence". Never `0%` when unmeasured. */
  readonly label: string;
  /** One sentence saying what that means and what to do about it. */
  readonly framing: string;
}

/**
 * A confidence as a percentage, matching `Confidence.__str__`'s
 * `format(value, ".0%")` so the same identification reads the same on both
 * sides of the wire.
 */
export function percentLabel(score: Confidence): string {
  return `${Math.round(score * 100)}%`;
}

/**
 * The wording for one candidate.
 *
 * There is deliberately **no confidence threshold** here. Nothing in the spec or
 * the ADRs calibrates one, and a threshold is not what makes this screen safe:
 * spec §20 requires the user to confirm at *every* confidence, so a 99% match
 * still takes a tap. What varies is how the screen states what it knows, and
 * `state` is the only distinction that is real — a measurement was taken, or it
 * was not.
 */
export function certaintyOf(candidate: ConfirmationCandidate): Certainty {
  if (candidate.outcome.kind === "identified") {
    return {
      state: "measured",
      label: percentLabel(candidate.outcome.confidence),
      framing:
        "That is how closely your photographs matched this card — a probability, " +
        "not a verdict. Check the set, number and variant against the card in your hand.",
    };
  }

  return {
    // Emphatically not "0%": zero is a measurement, and no measurement was
    // taken. Collapsing the two would report a confident negative where the
    // truth is that nothing has looked.
    state: "unknown",
    label: "Not measured",
    framing:
      "Nothing has identified this card — you chose it from the catalog yourself. " +
      "Check the set, number and variant against the card in your hand.",
  };
}

/** The confirmation gate for one card. */
export function identifyHref(cardId: string): string {
  return `/identify?card_id=${encodeURIComponent(cardId)}`;
}
