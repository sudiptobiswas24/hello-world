import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

// O570: the slab list offered to remove a band a posted run was paid at, and the server refused it in words. The
// list now offers removal only where the server would take it, read off the band (kept_because, O567's rule), and
// says why in its place where it would not.
let slabs: Record<string, unknown>[] = [];
vi.mock("../../auth/me", () => ({ useAccess: () => ({ can: () => true }) }));
vi.mock("../../api/hooks", () => ({
  useRecord: () => ({ data: { id: 4, code: "PTWB", name: "Professional tax", kind: "deduction", basis: "slab" },
    isError: false, refetch: () => undefined }),
  useRows: (endpoint: string) => ({ data: endpoint.includes("pay-component-slabs") ? slabs : [], isPending: false,
    isError: false }),
  useGet: () => ({ data: undefined, isPending: true, isError: false }),
  useAct: () => ({ pending: false, run: () => Promise.resolve({ ok: true, data: {} }) }),
}));
vi.mock("../../api/client", async (original) => ({
  ...(await original<typeof import("../../api/client")>()),
  get: () => new Promise(() => undefined),
  list: () => new Promise(() => undefined),
}));

import PayComponentForm from "./PayComponentForm";

afterEach(() => { cleanup(); slabs = []; });

function opened(rows: Record<string, unknown>[]) {
  slabs = rows;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={createMemoryRouter([{ path: "/payroll/pay-components/:id", element: <PayComponentForm /> }],
        { initialEntries: ["/payroll/pay-components/4"] })} />
    </QueryClientProvider>,
  );
}

const removers = (page: ReturnType<typeof render>) => page.container.querySelectorAll('button[aria-label="Remove the line"]');

describe("Removing a slab (O570)", () => {
  const band = { component: 4, month: null, state: "WB", effective_from: "2026-04-01", for_women: false,
    confirmed_on: "2026-03-31", schedule_bands: [] };
  const why = "PTWB over 10000 from 2026-04-01 is what PR-0001 was paid at; it is not deleted. Void that run first, "
    + "or give the new figure a row of its own from the day it applies.";

  it("is offered on a band no posted run was paid at", () => {
    const page = opened([{ ...band, id: 1, above: "0.00", up_to: "10000.00", amount: "0.00", kept_because: "" }]);
    expect(removers(page)).toHaveLength(1);
  });

  it("is not offered on a band a posted run kept, and the list says why in its place", () => {
    const page = opened([
      { ...band, id: 1, above: "0.00", up_to: "10000.00", amount: "0.00", kept_because: "" },
      { ...band, id: 2, above: "10000.00", up_to: null, amount: "110.00", kept_because: why },
    ]);
    expect(removers(page)).toHaveLength(1);
    expect(page.container.textContent).toContain(why);
  });
});
