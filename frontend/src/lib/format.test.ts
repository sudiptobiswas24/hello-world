import { describe, expect, it } from "vitest";

import { count, date, dateTime, isNegative, money, quantity } from "./format";

describe("money", () => {
  it("groups in lakhs and crores", () => {
    expect(money("12345678.9")).toBe("1,23,45,678.90");
    expect(money("1000")).toBe("1,000.00");
    expect(money("999")).toBe("999.00");
  });

  it("is exact where a float is not", () => {
    // 0.1 + 0.2 in a float is 0.30000000000000004.
    expect(money("0.3")).toBe("0.30");
    // Past 2^53 a float cannot even hold the rupees.
    expect(money("9007199254740993.01")).toBe("9,00,71,99,25,47,40,993.01");
  });

  it("rounds half away from zero, carrying through the digits", () => {
    expect(money("2.345")).toBe("2.35");
    expect(money("-2.345")).toBe("-2.35");
    expect(money("99.995")).toBe("100.00");
    expect(money("999999.999")).toBe("10,00,000.00");
  });

  it("shows no minus on a zero", () => {
    expect(money("-0.00")).toBe("0.00");
    expect(money("-0.001")).toBe("0.00");
  });

  it("is blank for nothing and for what is not a number", () => {
    expect(money(null)).toBe("");
    expect(money("")).toBe("");
    expect(money("abc")).toBe("");
  });
});

describe("quantity", () => {
  it("drops the zeros a column's scale adds", () => {
    expect(quantity("100.0000")).toBe("100");
    expect(quantity("12.5000")).toBe("12.5");
    expect(quantity("1234.125")).toBe("1,234.125");
  });

  it("keeps grams of a kilogramme", () => {
    expect(quantity("0.0005", 4)).toBe("0.0005");
    expect(quantity("0.00005", 4)).toBe("0.0001");
  });
});

describe("sign", () => {
  it("reads the sign from the string", () => {
    expect(isNegative("-5")).toBe(true);
    expect(isNegative("-0.00")).toBe(false);
    expect(isNegative("5")).toBe(false);
  });
});

describe("dates", () => {
  it("writes a date as written, whatever the browser's zone", () => {
    expect(date("2026-04-01")).toBe("01 Apr 2026");
    expect(date("")).toBe("");
  });

  it("writes a moment in the plant's time", () => {
    // 20:00 in Greenwich on 1 March is 01:30 on 2 March in Kolkata.
    expect(dateTime("2026-03-01T20:00:00Z")).toBe("02 Mar 2026, 01:30");
  });

  it("counts rows the same way", () => {
    expect(count(25000)).toBe("25,000");
    expect(count(1234567)).toBe("12,34,567");
  });
});
