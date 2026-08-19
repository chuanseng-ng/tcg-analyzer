import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyCardFailure } from "@/lib/card-errors";

/**
 * The gate and the detail view share this classifier, so these cases are the
 * reason it was extracted: "no card is recorded under that identifier" has to
 * mean the same thing on both, or the gate accepts an identifier the catalog
 * rejects.
 */
describe("classifyCardFailure", () => {
  it("reads the spec §66 code for an identifier naming no card as missing", () => {
    const error = new ApiError("gone", { status: 404, code: "card_not_identified" });

    expect(classifyCardFailure(error)).toBe("missing");
  });

  it("reads a 422 carrying no code as a malformed identifier, which is also missing", () => {
    // FastAPI's own request validation, which `errors.py` deliberately leaves
    // outside the envelope. To a reader it is the same fact: the link does not
    // lead to a card.
    expect(classifyCardFailure(new ApiError("bad id", { status: 422 }))).toBe("missing");
  });

  it("reads provider_error as the catalog being unreachable", () => {
    const error = new ApiError("down", {
      status: 503,
      code: "provider_error",
      details: { reason: "catalog_unreachable" },
    });

    expect(classifyCardFailure(error)).toBe("unreachable");
  });

  it("treats a request that never reached the server as unreachable too", () => {
    expect(classifyCardFailure(new ApiError("network"))).toBe("unreachable");
  });

  it("falls through to unexpected for a coded failure it does not recognise", () => {
    expect(classifyCardFailure(new ApiError("odd", { status: 500, code: "internal_error" }))).toBe(
      "unexpected",
    );
  });

  it("treats anything that is not an ApiError as unexpected", () => {
    expect(classifyCardFailure(new TypeError("boom"))).toBe("unexpected");
    expect(classifyCardFailure("not an error")).toBe("unexpected");
  });
});
