import { describe, expect, it } from "vitest";

import type { CardResponse } from "@/lib/api";
import {
  certaintyOf,
  confidence,
  identifiedFromImage,
  identifyHref,
  manuallySelected,
  percentLabel,
} from "@/lib/identification";

function card(overrides: Partial<CardResponse> = {}): CardResponse {
  return {
    id: "22222222-2222-2222-2222-222222222222",
    name: "Charizard",
    card_number: "4/102",
    game: "pokemon",
    language: "en",
    rarity: "Rare Holo",
    variant: "unlimited-holo",
    metadata: {},
    set: {
      id: "11111111-1111-1111-1111-111111111111",
      set_code: "BS",
      name: "Base Set",
      release_date: "1999-01-09",
      metadata: { total_cards: 102 },
    },
    external_ids: [{ provider: "manual", external_id: "bs-4-unlimited-holo" }],
    ...overrides,
  };
}

describe("confidence", () => {
  it.each([0, 0.5, 0.82, 1])("accepts %s, which lies in the closed unit interval", (value) => {
    expect(confidence(value)).toBe(value);
  });

  it.each([-0.1, 1.1, Number.NaN, Number.POSITIVE_INFINITY])(
    "rejects %s the way the domain's InvalidConfidence does",
    (value) => {
      expect(() => confidence(value)).toThrow(RangeError);
    },
  );
});

describe("candidates", () => {
  it("records a manual selection as carrying no identification at all", () => {
    // M1's only producer. The domain models "could not conclude" as an answer
    // returned *instead of* an identification, never as an identification with
    // a missing number, and this has to match it.
    const candidate = manuallySelected(card());

    expect(candidate.card.name).toBe("Charizard");
    expect(candidate.origin).toBe("manual_selection");
    expect(candidate.outcome.kind).toBe("insufficient_information");
  });

  it("carries an image identification with its measured confidence", () => {
    // The M2 seam. Nothing in the product calls this yet.
    const candidate = identifiedFromImage(card(), confidence(0.82));

    expect(candidate.origin).toBe("image_identification");
    expect(candidate.outcome).toMatchObject({ kind: "identified", confidence: 0.82 });
  });
});

describe("percentLabel", () => {
  it.each([
    [0, "0%"],
    [0.5, "50%"],
    [0.824, "82%"],
    [1, "100%"],
  ])("renders %s as %s, matching Confidence.__str__", (value, expected) => {
    expect(percentLabel(confidence(value))).toBe(expected);
  });
});

describe("certaintyOf", () => {
  it("states an absent identification as an explicit unknown, never as zero", () => {
    // The trap this test exists for: 0% is a *measurement* saying the card is
    // certainly not this one. No measurement was taken. Collapsing the two
    // would report a confident negative where the truth is that nothing looked.
    const certainty = certaintyOf(manuallySelected(card()));

    expect(certainty.state).toBe("unknown");
    expect(certainty.label).toBe("Not measured");
    expect(`${certainty.label} ${certainty.framing}`).not.toContain("%");
    expect(certainty.label).not.toMatch(/\d/);
  });

  it("says who chose the card when nothing identified it", () => {
    const { framing } = certaintyOf(manuallySelected(card()));

    expect(framing).toMatch(/nothing has identified this card/i);
    expect(framing).toMatch(/you chose it from the catalog yourself/i);
    expect(framing).toMatch(/set, number and variant/i);
  });

  it("frames a measured confidence as a probability rather than a verdict", () => {
    const certainty = certaintyOf(identifiedFromImage(card(), confidence(0.94)));

    expect(certainty.state).toBe("measured");
    expect(certainty.label).toBe("94%");
    expect(certainty.framing).toMatch(/probability/i);
    expect(certainty.framing).not.toMatch(/\bcertain\b|\bconfirmed\b/i);
  });

  it("never reports a state that would let the screen call itself settled", () => {
    // Spec §20 requires a confirmation at *every* confidence, so there is no
    // band above which the interface may stop asking. `state` distinguishes
    // only whether a measurement exists — the one thing that is actually true.
    for (const value of [0, 0.4, 0.6, 0.85, 0.99, 1]) {
      expect(certaintyOf(identifiedFromImage(card(), confidence(value))).state).toBe("measured");
    }
  });
});

describe("identifyHref", () => {
  it("points at the confirmation gate for one card", () => {
    expect(identifyHref("22222222-2222-2222-2222-222222222222")).toBe(
      "/identify?card_id=22222222-2222-2222-2222-222222222222",
    );
  });

  it("encodes an identifier that would otherwise break the query string", () => {
    expect(identifyHref("a b&c")).toBe("/identify?card_id=a%20b%26c");
  });
});
