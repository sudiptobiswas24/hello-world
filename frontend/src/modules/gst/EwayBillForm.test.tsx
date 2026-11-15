import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

// O578: the screen offered "Or job-work delivery" with no value going back, and the server refuses a job-work
// delivery's e-way bill without one ("State the goods' value going back..."): none could be made from here.
const sent: { method: string; path: string; body: Record<string, unknown> }[] = [];
vi.mock("../../auth/me", () => ({ useAccess: () => ({ can: () => true }) }));
vi.mock("../../api/hooks", () => ({
  useRecord: () => ({ data: undefined, isError: false, refetch: () => undefined }),
  useRows: () => ({ data: [], isPending: false, isError: false }),
  useGet: () => ({ data: undefined, isPending: true, isError: false }),
  useAct: () => ({
    pending: false,
    run: (method: string, path: string, body: Record<string, unknown>) => {
      sent.push({ method, path, body });
      return Promise.resolve({ ok: false, error: { fields: {} } });
    },
  }),
}));
vi.mock("../../api/client", async (original) => ({
  ...(await original<typeof import("../../api/client")>()),
  get: () => new Promise(() => undefined),
  list: () => new Promise(() => undefined),
}));

import EwayBillForm from "./EwayBillForm";

afterEach(() => { cleanup(); sent.length = 0; });

function opened() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={createMemoryRouter([{ path: "/accounts/eway-bills/:id", element: <EwayBillForm /> }],
        { initialEntries: ["/accounts/eway-bills/new"] })} />
    </QueryClientProvider>,
  );
}

describe("A job-work delivery's e-way bill from the screen (O578)", () => {
  it("asks the value going back, and sends it with the delivery", async () => {
    const page = opened();
    const box = page.getByLabelText("Value going back") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "250000" } });
    await act(async () => { fireEvent.click(page.getByText("Create")); });
    expect(sent).toHaveLength(1);
    const [post] = sent;
    expect(post?.method).toBe("POST");
    expect(post?.path).toBe("/api/gst/eway-bills/");
    expect(String(post?.body.declared_value)).toMatch(/^250000(\.00)?$/);
  });
});
