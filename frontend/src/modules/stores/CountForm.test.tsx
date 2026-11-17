import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

// An adder sends its fields and nothing else. ActionForm handed a declared body the form's own state, which
// blank() starts as { id: 0 }, so `body: (values) => values` sent an `id` too; once the count's add action named the
// keys it takes (O579) it refused it, and no line could be counted from the sheet (nor a role or a quote added).
const sent: { path: string; body: unknown }[] = [];
vi.mock("../../auth/me", () => ({ useAccess: () => ({ can: () => true }) }));
vi.mock("../../api/hooks", () => ({
  useRecord: () => ({ data: { id: 7, number: "SC-0007", posted: false, lines: [] }, isError: false,
    refetch: () => undefined }),
  useRows: () => ({ data: [], isPending: false, isError: false }),
  useGet: () => ({ data: undefined, isPending: true, isError: false }),
  useAct: () => ({ pending: false, run: (_method: string, path: string, body: unknown) => {
    sent.push({ path, body });
    return Promise.resolve({ ok: true, data: {} });
  } }),
}));
vi.mock("../../api/client", async (original) => ({
  ...(await original<typeof import("../../api/client")>()),
  get: () => new Promise(() => undefined),
  list: () => new Promise(() => undefined),
}));

import CountForm from "./CountForm";

afterEach(() => { cleanup(); sent.length = 0; });

describe("Adding a count from the sheet", () => {
  it("sends the adder's fields and not the form's id", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const page = render(
      <QueryClientProvider client={client}>
        <RouterProvider router={createMemoryRouter([{ path: "/stores/counts/:id", element: <CountForm /> }],
          { initialEntries: ["/stores/counts/7"] })} />
      </QueryClientProvider>,
    );
    fireEvent.click(await page.findByRole("button", { name: "Add a count" }));
    const form = await page.findByRole("form", { name: "Add a count" });
    fireEvent.submit(form);
    await waitFor(() => expect(sent).toHaveLength(1));
    const [only] = sent;
    expect(only?.path).toBe("/api/inventory/stock-counts/7/add/");
    expect(Object.keys((only?.body ?? {}) as object).sort()).toEqual(["bin", "counted_quantity", "item", "lot"]);
  });
});
