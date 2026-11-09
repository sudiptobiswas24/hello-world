import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccessProvider } from "../auth/me";
import { AddLine, lineBody, type NewLine } from "./Lines";

/**
 * O495: the add row offered an item only, so freight, packing or handling could be put on a document through the
 * API alone. Where the document takes a charge and the person may read the charges, it adds one: the charges kept
 * for the document's side, described by the charge's name, one of it, at the price typed, and no taxes named so the
 * server gives it the charge's.
 */

const FREIGHT = { id: 7, code: "FRT", name: "Freight" };

function serve(permissions: string[]) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    asked.push(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (input.startsWith("/api/core/me/")) {
      return json({ id: 1, username: "asha", name: "Asha", is_superuser: false, roles: [], permissions, company: "Test Co",
        time_zone: "Asia/Kolkata" });
    }
    if (input.startsWith("/api/accounting/charge-types/")) return json([FREIGHT]);
    return json([]);
  }));
  return asked;
}

function show(side: "sales" | "purchase", onAdd: (line: NewLine) => boolean, charges = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccessProvider fallback={<p>Opening...</p>}>
        <AddLine side={side} charges={charges} onAdd={onAdd} />
      </AccessProvider>
    </QueryClientProvider>,
  );
}

async function chooseFreight() {
  fireEvent.click(await screen.findByRole("button", { name: "Charge" }));
  const box = screen.getByRole("combobox", { name: "Charge to add" });
  fireEvent.focus(box);
  fireEvent.change(box, { target: { value: "Fre" } });
  fireEvent.mouseDown(await screen.findByRole("option", { name: /FRT · Freight/ }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the add row choosing a charge (O495)", () => {
  it("adds freight at the price typed, named by its charge, with no taxes named", async () => {
    const asked = serve(["accounting.view_chargetype"]);
    const added: NewLine[] = [];
    show("sales", (line) => { added.push(line); return true; });
    await chooseFreight();
    expect(screen.getByLabelText("Description to add")).toHaveProperty("value", "Freight");
    expect(screen.getByLabelText("Quantity to add")).toHaveProperty("value", "1");
    // A charge has no price list: the row is not taken until its amount is typed.
    expect(screen.getByRole("button", { name: "Add line" })).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("Unit price to add"), { target: { value: "1000.00" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add line" })); });
    expect(added).toHaveLength(1);
    expect(lineBody(added[0]!)).toEqual({ charge: 7, quantity: "1", description: "Freight", unit_price: "1000.00" });
    expect(added[0]!.label).toBe("Freight");
    // Only the charges kept for a sale: those with an account to book the revenue to.
    const listed = asked.filter((url) => url.startsWith("/api/accounting/charge-types/?"));
    expect(listed.length).toBeGreaterThan(0);
    for (const url of listed) {
      expect(url).toContain("revenue_account__isnull=false");
      expect(url).toContain("is_active=true");
    }
  });

  it("asks a purchase for the charges kept for buying, and keeps a retyped description", async () => {
    const asked = serve(["accounting.view_chargetype"]);
    const added: NewLine[] = [];
    show("purchase", (line) => { added.push(line); return true; });
    await chooseFreight();
    fireEvent.change(screen.getByLabelText("Description to add"), { target: { value: "Freight from Vapi" } });
    fireEvent.change(screen.getByLabelText("Unit price to add"), { target: { value: "1000.00" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add line" })); });
    expect(lineBody(added[0]!)).toEqual({ charge: 7, quantity: "1", description: "Freight from Vapi", unit_price: "1000.00" });
    const listed = asked.filter((url) => url.startsWith("/api/accounting/charge-types/?"));
    expect(listed.length).toBeGreaterThan(0);
    for (const url of listed) expect(url).toContain("expense_account__isnull=false");
  });

  it("offers no charge to a person who may not read them, and never asks for the list", async () => {
    const asked = serve([]);
    show("sales", () => true);
    expect(await screen.findByRole("combobox", { name: "Item to add" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Charge" })).toBeNull();
    await waitFor(() => expect(asked.some((url) => url.startsWith("/api/core/me/"))).toBe(true));
    expect(asked.some((url) => url.startsWith("/api/accounting/charge-types/"))).toBe(false);
  });

  it("offers no charge where the document's lines take none", async () => {
    serve(["accounting.view_chargetype"]);
    show("sales", () => true, false);
    expect(await screen.findByRole("combobox", { name: "Item to add" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Charge" })).toBeNull();
  });

  it("still sends an item line as before", () => {
    expect(lineBody({ key: 1, item: 3, label: "WDG-1 · Widget", quantity: "2", unit_price: "", uom: 4 }))
      .toEqual({ item: 3, quantity: "2", uom: 4 });
  });
});
