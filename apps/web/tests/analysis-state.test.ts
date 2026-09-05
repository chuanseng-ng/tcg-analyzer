import { describe, expect, it } from "vitest";

import { ANALYSIS_STATUSES, isFailed, isTerminal, isWorking, stepCopy } from "@/lib/analysis-state";

describe("spec §65's states", () => {
  it("names all nine, in the order the specification lists them", () => {
    expect(ANALYSIS_STATUSES).toEqual([
      "created",
      "uploading",
      "uploaded",
      "identifying",
      "awaiting_confirmation",
      "analyzing",
      "calculating",
      "completed",
      "failed",
    ]);
  });

  it("treats only the two terminal states as finished", () => {
    expect(ANALYSIS_STATUSES.filter(isTerminal)).toEqual(["completed", "failed"]);
    expect(ANALYSIS_STATUSES.filter(isWorking)).toHaveLength(7);
  });

  it("knows a failure from a completion", () => {
    expect(isFailed("failed")).toBe(true);
    expect(isFailed("completed")).toBe(false);
  });

  it("reads a state it has never heard of as still working, never as finished", () => {
    // A tenth state added server-side must not make a client claim a result
    // exists; the honest reading is "not done yet".
    expect(isTerminal("re_grading")).toBe(false);
    expect(isWorking("re_grading")).toBe(true);
  });
});

describe("stepCopy", () => {
  it("has a sentence for every state", () => {
    for (const status of ANALYSIS_STATUSES) {
      expect(stepCopy(status)).toMatch(/\S/);
    }
  });

  it("names a state it has no words for rather than going quiet", () => {
    expect(stepCopy("re_grading")).toContain("re_grading");
  });
});
