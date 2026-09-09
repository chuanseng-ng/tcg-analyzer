import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyFeedbackFailure } from "@/lib/feedback-errors";

/**
 * The feedback routes answer four different facts with one 404, so the
 * classifier must not invent a fifth by telling them apart. What it does have
 * to separate is the 409 — a code already minted, which is terminal — from
 * everything a user can usefully try again.
 */

function refusal(status: number, options: { code?: string; retryAfterSeconds?: number } = {}) {
  return new ApiError("refused", { status, ...options });
}

describe("classifyFeedbackFailure", () => {
  it("reads a request that never left as a retry", () => {
    expect(classifyFeedbackFailure(new Error("offline")).action).toBe("retry");
    expect(classifyFeedbackFailure(new ApiError("unreachable")).action).toBe("retry");
  });

  it("counts down a throttled request rather than offering a button", () => {
    const failure = classifyFeedbackFailure(refusal(429, { retryAfterSeconds: 30 }));
    expect(failure.action).toBe("wait");
    expect(failure.retryAfterSeconds).toBe(30);
  });

  it("carries no countdown when the service did not say how long", () => {
    expect(classifyFeedbackFailure(refusal(429)).retryAfterSeconds).toBeUndefined();
  });

  it("reads every 404 the same way, because the service does", () => {
    // Unknown, expired, already answered and not-a-code-at-all arrive
    // identically on purpose: a well-formed guess must learn nothing a
    // malformed one would not.
    expect(classifyFeedbackFailure(refusal(404)).action).toBe("gone");
  });

  it("separates a code that was already minted from anything worth retrying", () => {
    const failure = classifyFeedbackFailure(refusal(409));
    expect(failure.action).toBe("issued");
    expect(failure.message).toContain("shown once");
  });

  it("reads a refused grade as a retry", () => {
    expect(classifyFeedbackFailure(refusal(422)).action).toBe("retry");
  });

  it("reads a store that would not answer as a retry", () => {
    expect(classifyFeedbackFailure(refusal(503, { code: "provider_error" })).action).toBe("retry");
  });

  it("names no code in any message", () => {
    // Every branch is copy for a person. A developer message reaching the DOM
    // beside a bearer capability is the one thing this screen cannot do.
    for (const status of [404, 409, 422, 429, 503]) {
      expect(classifyFeedbackFailure(refusal(status)).message).not.toMatch(/[45]0\d/);
    }
  });
});
