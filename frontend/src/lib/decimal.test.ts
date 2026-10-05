import { describe, expect, it } from "vitest";

import { aboveZero, fromPaise, least, minus, positive, sum, toPaise } from "./decimal";

describe("money in paise", () => {
  it("adds what a float would not", () => {
    // In floats 0.1 + 0.2 is 0.30000000000000004.
    expect(sum(["0.1", "0.2"])).toBe("0.30");
    expect(sum(["12697.00", "1250.50", "-0.50"])).toBe("13947.00");
  });

  it("takes away exactly", () => {
    expect(minus("1000.00", "999.99")).toBe("0.01");
    expect(minus("100", "250.75")).toBe("-150.75");
  });

  it("rounds a third place half away from zero, as the server does", () => {
    expect(toPaise("2.345")).toBe(235n);
    expect(toPaise("-2.345")).toBe(-235n);
    expect(fromPaise(-5n)).toBe("-0.05");
  });

  it("knows the smaller and whether anything is left", () => {
    expect(least("500.00", "320.50")).toBe("320.50");
    expect(positive("0.00")).toBe(false);
    expect(positive("0.01")).toBe(true);
  });

  it("refuses what is not an amount", () => {
    expect(() => toPaise("12a")).toThrow();
  });
});

describe("quantities above zero", () => {
  it("sees what paise would round away", () => {
    expect(aboveZero("0.0040")).toBe(true);
    expect(positive("0.004")).toBe(false);
  });

  it("is not fooled by zeros, signs or nothing", () => {
    expect(aboveZero("0.0000")).toBe(false);
    expect(aboveZero("-3.5")).toBe(false);
    expect(aboveZero("")).toBe(false);
    expect(aboveZero(undefined)).toBe(false);
    expect(aboveZero("10")).toBe(true);
    expect(() => aboveZero("1e3")).toThrow();
  });
});
