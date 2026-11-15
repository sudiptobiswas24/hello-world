import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

// O567: a statutory figure a posted run was paid at offered Delete, and the server refused it in words. The screen
// now offers Delete only where the server would take it, read off the record (kept_because, O556's rule), and says
// why where it would not.
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

import StatutoryFigureForm from "./StatutoryFigureForm";

afterEach(() => { cleanup(); record = {}; });

function opened(row: Record<string, unknown>) {
  record = row;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={createMemoryRouter([{ path: "/payroll/statutory-figures/:id", element: <StatutoryFigureForm /> }],
        { initialEntries: ["/payroll/statutory-figures/7"] })} />
    </QueryClientProvider>,
  );
}

const buttons = (page: ReturnType<typeof render>) =>
  Array.from(page.container.querySelectorAll("button")).map((button) => button.textContent);

describe("Delete on a statutory figure (O567)", () => {
  const figure = { id: 7, kind: "bonus_floor", state: "", effective_from: "2025-11-21", value: "7000.0000",
    source: "test row", confirmed_on: "2025-11-22" };

  it("is offered on a figure no posted run was paid at", () => {
    expect(buttons(opened({ ...figure, kept_because: "" }))).toContain("Delete");
  });

  it("is not offered on a figure a posted run kept, and the screen says why", () => {
    const why = "Bonus calculation floor 7000 from 2025-11-21 is what PR-0001 was paid at; it is not deleted. Void "
      + "that run first, or give the new figure a row of its own from the day it applies.";
    const page = opened({ ...figure, kept_because: why });
    expect(buttons(page)).not.toContain("Delete");
    expect(page.container.textContent).toContain(why);
  });
});
