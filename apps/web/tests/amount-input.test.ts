import { describe, expect, it } from "vitest";

import { parseAmount, parsePercent, proportionToPercent } from "@/lib/amount-input";

describe("parseAmount", () => {
  it("reads blank as blank, which is not zero", () => {
    // Spec §45: an absent acquisition cost is `null`, and `"0.00"` is a real
    // one. If these two collapsed here, "I don't remember" would become "it
    // was free" long before the request was built.
    expect(parseAmount("")).toEqual({ state: "blank" });
    expect(parseAmount("   ")).toEqual({ state: "blank" });
    expect(parseAmount("0.00")).toEqual({ state: "amount", value: "0.00" });
  });

  it("takes whole and two-place amounts, trimmed", () => {
    expect(parseAmount("40")).toEqual({ state: "amount", value: "40" });
    expect(parseAmount(" 39.95 ")).toEqual({ state: "amount", value: "39.95" });
    expect(parseAmount("0.5")).toEqual({ state: "amount", value: "0.5" });
  });

  it("refuses anything the cent cannot hold, rather than rounding it", () => {
    // `Money` would quantise `5.005` to `5.01`. A field that accepted it would
    // have stored a number the user never typed.
    expect(parseAmount("5.005")).toEqual({ state: "invalid" });
  });

  it("refuses a negative rather than clamping it", () => {
    // Every line item and the acquisition cost are non-negative server-side;
    // clamping would submit a figure nobody entered.
    expect(parseAmount("-1")).toEqual({ state: "invalid" });
    expect(parseAmount("-0.00")).toEqual({ state: "invalid" });
  });

  it("refuses currency symbols, separators and anything else typed by hand", () => {
    for (const raw of ["S$40", "1,000", "40.00 SGD", "forty", "4e2", "40..0", "."]) {
      expect(parseAmount(raw)).toEqual({ state: "invalid" });
    }
  });
});

describe("parsePercent", () => {
  it("shifts the point rather than dividing, so no float is involved", () => {
    expect(parsePercent("10")).toEqual({ state: "amount", value: "0.1000" });
    expect(parsePercent("12.5")).toEqual({ state: "amount", value: "0.1250" });
    // 7.35 / 100 is not exact in binary. The digits are.
    expect(parsePercent("7.35")).toEqual({ state: "amount", value: "0.0735" });
    expect(parsePercent("0.05")).toEqual({ state: "amount", value: "0.0005" });
    expect(parsePercent("0")).toEqual({ state: "amount", value: "0.0000" });
  });

  it("takes the whole sale but no more", () => {
    // ADR 0007: the rate is a proportion in [0, 1]. A fee may take all of a
    // sale and nothing outside that.
    expect(parsePercent("100")).toEqual({ state: "amount", value: "1.0000" });
    expect(parsePercent("100.01")).toEqual({ state: "invalid" });
    expect(parsePercent("101")).toEqual({ state: "invalid" });
  });

  it("reads blank as blank", () => {
    expect(parsePercent("")).toEqual({ state: "blank" });
  });

  it("refuses more precision than the column holds", () => {
    // `selling_fee_rate` is Numeric(6, 4): two places of percent is all of it.
    expect(parsePercent("10.005")).toEqual({ state: "invalid" });
  });
});

describe("proportionToPercent", () => {
  it("reads a stored rate back in the units the user typed", () => {
    // The API returns four places because the column has four. Showing
    // "0.1000" beside a field labelled % would be showing a different number.
    expect(proportionToPercent("0.1000")).toBe("10");
    expect(proportionToPercent("0.1250")).toBe("12.5");
    expect(proportionToPercent("0.0735")).toBe("7.35");
    expect(proportionToPercent("0.0005")).toBe("0.05");
    expect(proportionToPercent("1.0000")).toBe("100");
    expect(proportionToPercent("0.0000")).toBe("0");
  });

  it("round-trips whatever parsePercent produced", () => {
    for (const typed of ["0", "5", "10", "12.5", "7.35", "0.05", "100"]) {
      const parsed = parsePercent(typed);
      expect(parsed.state).toBe("amount");
      if (parsed.state !== "amount") continue;
      expect(proportionToPercent(parsed.value)).toBe(typed);
    }
  });
});
