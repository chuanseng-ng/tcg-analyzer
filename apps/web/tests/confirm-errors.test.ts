import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyConfirmFailure } from "@/lib/confirm-errors";

describe("a confirmation the service did not record", () => {
  it("treats a 409 as not ready yet, and keeps the tap on offer", () => {
    // The worker has not reached the confirmation gate. Deliberately *not* the
    // upload's reading of 409, where the same status means "start a new
    // analysis" — one status, two endpoints, two meanings.
    const failure = classifyConfirmFailure(new ApiError("conflict", { status: 409 }));

    expect(failure.action).toBe("retry");
    expect(failure.message).toMatch(/not ready/i);
  });

  it("counts down a throttled confirmation rather than offering a button", () => {
    const failure = classifyConfirmFailure(
      new ApiError("slow down", { status: 429, retryAfterSeconds: 12 }),
    );

    expect(failure.action).toBe("wait");
    expect(failure.retryAfterSeconds).toBe(12);
  });

  it("carries no wait when the service did not say how long", () => {
    const failure = classifyConfirmFailure(new ApiError("slow down", { status: 429 }));

    expect(failure.action).toBe("wait");
    expect(failure).not.toHaveProperty("retryAfterSeconds");
  });

  it("treats one 404 as the session being gone, whichever of the four it was", () => {
    const failure = classifyConfirmFailure(new ApiError("nope", { status: 404 }));

    expect(failure.action).toBe("gone");
  });

  it("says a card the catalog does not hold in the catalog's own words", () => {
    const failure = classifyConfirmFailure(
      new ApiError("nope", { status: 404, code: "card_not_identified" }),
    );

    expect(failure.action).toBe("gone");
    expect(failure.message).toBe("No card is recorded under that identifier.");
  });

  it("reads a request that never left as the service not answering", () => {
    const failure = classifyConfirmFailure(new ApiError("offline"));

    expect(failure.action).toBe("retry");
    expect(failure.message).toMatch(/not answering/i);
  });

  it("reads an unreachable dependency the same way", () => {
    const failure = classifyConfirmFailure(
      new ApiError("down", { status: 503, code: "provider_error" }),
    );

    expect(failure.action).toBe("retry");
  });

  it("does not pretend to understand something that is not an ApiError", () => {
    const failure = classifyConfirmFailure(new TypeError("boom"));

    expect(failure.action).toBe("retry");
  });

  it("keeps an answer it did not understand separate from an outage", () => {
    const failure = classifyConfirmFailure(new ApiError("odd", { status: 418 }));

    expect(failure.action).toBe("retry");
    expect(failure.message).toMatch(/did not understand/i);
  });

  it("does not invite a retry that can never succeed", () => {
    // The gate refused the photographs (#36). Spec §65 has no edge out of
    // `failed`, so "try again" would be a loop with no exit — the reason this
    // branch exists at all.
    const failure = classifyConfirmFailure(
      new ApiError("conflict", { status: 409, code: "image_quality_failure" }),
    );

    expect(failure.action).toBe("gone");
    expect(failure.message).toMatch(/could not be analysed/i);
  });

  it("does not blame the photographs for a failure that was not theirs", () => {
    const failure = classifyConfirmFailure(
      new ApiError("conflict", { status: 409, code: "analysis_failed" }),
    );

    expect(failure.action).toBe("gone");
    expect(failure.message).not.toMatch(/photograph/i);
  });

  it("offers no way into the upload screen, even when new photographs are what is needed", () => {
    // #91: the confirmation gate has no route onward to analysis in any branch,
    // and a failure is not the place to open one.
    for (const code of ["image_quality_failure", "analysis_failed"] as const) {
      const failure = classifyConfirmFailure(new ApiError("conflict", { status: 409, code }));

      expect(failure.message).not.toMatch(/\/analyze/);
    }
  });
});
