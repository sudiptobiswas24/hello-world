import { describe, expect, it } from "vitest";

import { cell } from "./ListView";

describe("a money cell", () => {
  const total = { key: "total", label: "Total", kind: "money" as const };

  it("names the currency of a document that is not in the base (O611)", () => {
    expect(cell(total, { total: "1370.00", foreign_currency: "USD" })).toBe("1,370.00 USD");
  });

  it("names none for a document in the base, or a row that says nothing of one", () => {
    expect(cell(total, { total: "1180.00", foreign_currency: null })).toBe("1,180.00");
    expect(cell(total, { total: "1180.00" })).toBe("1,180.00");
  });
});
