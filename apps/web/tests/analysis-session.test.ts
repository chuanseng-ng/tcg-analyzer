import { afterEach, describe, expect, it, vi } from "vitest";

import { currentAnalysis, forgetAnalysis, rememberAnalysis } from "@/lib/analysis-session";

const ANALYSIS_ID = "33333333-3333-3333-3333-333333333333";

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("the analysis this tab is working on", () => {
  it("survives the trip from /analyze to /identify", () => {
    rememberAnalysis(ANALYSIS_ID);

    expect(currentAnalysis()).toBe(ANALYSIS_ID);
  });

  it("is absent before anything has been uploaded", () => {
    expect(currentAnalysis()).toBeNull();
  });

  it("is forgotten when the user starts over", () => {
    rememberAnalysis(ANALYSIS_ID);

    forgetAnalysis();

    expect(currentAnalysis()).toBeNull();
  });

  it("reads a blank value as nothing", () => {
    window.sessionStorage.setItem("tcg.analysis", "   ");

    expect(currentAnalysis()).toBeNull();
  });

  it("reads a refused store as nothing, rather than throwing", () => {
    // Safari's private mode throws on access rather than returning null, and a
    // screen that cannot reach storage must still render.
    vi.stubGlobal("sessionStorage", {
      get getItem(): never {
        throw new DOMException("denied");
      },
    });

    expect(() => rememberAnalysis(ANALYSIS_ID)).not.toThrow();
    expect(currentAnalysis()).toBeNull();
    expect(() => forgetAnalysis()).not.toThrow();
  });
});
