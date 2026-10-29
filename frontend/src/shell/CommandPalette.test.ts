import { describe, expect, it } from "vitest";

import { rank, type Command } from "./CommandPalette";

const commands: Command[] = [
  { label: "Home", hint: "Start", href: "/" },
  { label: "Invoices", hint: "Sales", href: "/sales/invoices", keywords: "bill credit note customer" },
  { label: "Sales orders", hint: "Sales", href: "/sales/orders", keywords: "so order customer" },
  { label: "Customers", hint: "Sales", href: "/sales/customers", keywords: "party buyer" },
];

const hrefs = (typed: string) => rank(commands, typed).map((command) => command.href);

describe("ranking what was typed", () => {
  it("puts the screen named that first, before those that only mention it", () => {
    expect(hrefs("cust")[0]).toBe("/sales/customers");
    expect(hrefs("cust")).toEqual(["/sales/customers", "/sales/invoices", "/sales/orders"]);
  });

  it("prefers a name that starts with it to one that has it later", () => {
    expect(hrefs("ord")[0]).toBe("/sales/orders");
  });

  it("needs every word to match somewhere", () => {
    expect(hrefs("sales ord")).toEqual(["/sales/orders"]);
    expect(hrefs("zzz")).toEqual([]);
  });

  it("keeps the list's order when nothing is typed", () => {
    expect(hrefs("")).toEqual(commands.map((command) => command.href));
  });

  it("matches the start of a word, not the middle", () => {
    expect(hrefs("voice")).toEqual([]);
  });
});
