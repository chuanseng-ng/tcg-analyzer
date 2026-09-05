import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { classifyResultsFailure } from "@/lib/results-errors";

describe("classifyResultsFailure", () => {
  it("sends a lost analysis back to the start", () => {
    // One 404 covers an unknown analysis, someone else's, a missing cookie and
    // a lapsed one (#32); none of them comes back by reading again.
    expect(classifyResultsFailure(new ApiError("gone", { status: 404 })).action).toBe("restart");
  });

  it("offers a retry when a store would not answer", () => {
    const store = new ApiError("down", {
      status: 503,
      code: "provider_error",
      details: { reason: "market_store_unreachable" },
    });

    expect(classifyResultsFailure(store).action).toBe("retry");
  });

  it("offers a retry when the request never reached the service", () => {
    expect(classifyResultsFailure(new ApiError("offline")).action).toBe("retry");
    expect(classifyResultsFailure(new TypeError("fetch failed")).action).toBe("retry");
  });

  it("does not pretend an unexplained answer will come right on a retry", () => {
    expect(classifyResultsFailure(new ApiError("broken", { status: 500 })).action).toBe(
      "unexpected",
    );
  });

  it("never returns a developer message as copy", () => {
    const failure = classifyResultsFailure(new ApiError("The API at http://x returned 500."));

    expect(failure.message).not.toMatch(/http|API at/);
  });
});
