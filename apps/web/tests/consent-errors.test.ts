import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyConsentFailure } from "@/lib/consent-errors";

/**
 * The consent routes answer three different facts with one 404, so the
 * classifier must not invent a fourth by telling them apart. What it does have
 * to separate is the 409 — photographs already kept, which is terminal — from
 * everything a user can usefully try again.
 */

function refusal(status: number, options: { code?: string; retryAfterSeconds?: number } = {}) {
  return new ApiError("refused", { status, ...options });
}

describe("classifyConsentFailure", () => {
  it("reads a request that never left as a retry", () => {
    expect(classifyConsentFailure(new Error("offline")).action).toBe("retry");
    expect(classifyConsentFailure(new ApiError("unreachable")).action).toBe("retry");
  });

  it("counts down a throttled request rather than offering a button", () => {
    const failure = classifyConsentFailure(refusal(429, { retryAfterSeconds: 30 }));
    expect(failure.action).toBe("wait");
    expect(failure.retryAfterSeconds).toBe(30);
  });

  it("carries no countdown when the service did not say how long", () => {
    expect(classifyConsentFailure(refusal(429)).retryAfterSeconds).toBeUndefined();
  });

  it("reads every 404 the same way, because the service does", () => {
    // Unknown, mistyped and already-withdrawn arrive identically on purpose,
    // and there is no read route to tell them apart with: a well-formed guess
    // must learn nothing a malformed one would not.
    expect(classifyConsentFailure(refusal(404)).action).toBe("gone");
  });

  it("separates photographs already kept from anything worth retrying", () => {
    const failure = classifyConsentFailure(refusal(409));
    expect(failure.action).toBe("kept");
    expect(failure.message).toContain("shown once");
  });

  it("reads a store outage as a retry, and says nothing was kept", () => {
    const failure = classifyConsentFailure(refusal(503, { code: "provider_error" }));
    expect(failure.action).toBe("retry");
    // The reassurance is the point: a failure to consent must never read as
    // "your photographs went somewhere and we are not sure where".
    expect(failure.message).toContain("Nothing has been kept");
  });

  it("falls back to a retry for a status it has no reading of", () => {
    expect(classifyConsentFailure(refusal(418)).action).toBe("retry");
  });
});
