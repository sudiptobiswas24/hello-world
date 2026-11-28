import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

// O556: a confirmed bank rate offered Delete, and the server refused it in words. The screen now offers Delete
// only where the server would take it, read off the record (kept_because), and says why where it would not.
let record: Record<string, unknown> = {};
vi.mock("../../auth/me", () => ({ useAccess: () => ({ can: () => true }) }));
vi.mock("../../api/hooks", () => ({
  useRecord: () => ({ data: record, isError: false, refetch: () => undefined }),
  useGet: () => ({ data: undefined, isPending: true, isError: false }),
  useAct: () => ({ pending: false, run: () => Promise.resolve({ ok: true, data: {} }) }),
}));
vi.mock("../../api/client", async (original) => ({
  ...(await original<typeof import("../../api/client")>()),
  get: () => new Promise(() => undefined),
  list: () => new Promise(() => undefined),
}));

import BankRateForm from "./BankRateForm";
import TcsRateForm from "./TcsRateForm";
import TdsRateForm from "./TdsRateForm";

afterEach(() => { cleanup(); record = {}; });

function opened(Form: () => ReactNode, row: Record<string, unknown>) {
  record = row;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={createMemoryRouter([{ path: "/settings/rates/:id", element: <Form /> }],
        { initialEntries: ["/settings/rates/3"] })} />
    </QueryClientProvider>,
  );
}

const buttons = (page: ReturnType<typeof render>) =>
  Array.from(page.container.querySelectorAll("button")).map((button) => button.textContent);

describe("Delete on a dated rate (O556)", () => {
  const bank = { id: 3, applies_from: "2026-04-01", rate_percent: "6.5000", authority: "RBI/2026/12" };

  it("is offered on a bank rate to confirm, beside Confirm", () => {
    const page = opened(BankRateForm, { ...bank, confirmed_on: null, kept_because: "" });
    expect(buttons(page)).toContain("Delete");
    expect(buttons(page)).toContain("Confirm");
  });

  it("is not offered once the bank rate is confirmed, and the screen says why", () => {
    const why = "Bank rate 6.5% from 2026-04-01 is confirmed, and the MSME interest shown since was worked at it.";
    const page = opened(BankRateForm, { ...bank, confirmed_on: "2026-04-02", kept_because: why });
    expect(buttons(page)).not.toContain("Delete");
    expect(buttons(page)).not.toContain("Confirm");
    expect(page.container.textContent).toContain(why);
  });

  it("follows the server on a TDS or TCS rate: confirmed and unused may go, taken at may not", () => {
    const tds = { id: 3, section: 1, section_code: "194C", applies_from: "2026-04-01", rate_percent: "1.0000",
      no_pan_rate_percent: "20.0000", confirmed_on: "2026-04-02" };
    for (const Form of [TdsRateForm, TcsRateForm]) {
      expect(buttons(opened(Form, { ...tds, kept_because: "" }))).toContain("Delete");
      cleanup();
      const page = opened(Form, { ...tds, kept_because: "Bills were deducted at this rate; it stays." });
      expect(buttons(page)).not.toContain("Delete");
      expect(page.container.textContent).toContain("Bills were deducted at this rate; it stays.");
      cleanup();
    }
  });
});
