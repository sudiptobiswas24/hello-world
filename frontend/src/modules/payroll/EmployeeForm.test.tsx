import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const sent: string[] = [];
let allowed = true;
vi.mock("../../auth/me", () => ({ useAccess: () => ({ can: () => allowed }) }));
vi.mock("../../api/hooks", () => ({
  useAct: () => ({
    pending: false,
    run: (_method: string, path: string) => { sent.push(path); return Promise.resolve({ ok: true, data: { id: 7, pin: "482910", has_pin: true } }); },
  }),
}));

import { StationPin } from "./EmployeeForm";

afterEach(() => { cleanup(); sent.length = 0; allowed = true; });

describe("the station PIN on the employee screen", () => {
  it("issues one and shows it once, until dismissed", async () => {
    const page = render(<StationPin employee={{ id: 7, name: "Ravi", has_pin: false, employment_status: "active" }} />);
    await act(async () => { fireEvent.click(page.getByText("Issue station PIN")); });
    expect(sent).toEqual(["/api/hr/employees/7/issue-pin/"]);
    expect(page.getByText("482910")).toBeTruthy();
    expect(page.container.textContent).toContain("Write it down now");
    // The record read again after the action (now holding a PIN) keeps the PIN on screen.
    page.rerender(<StationPin employee={{ id: 7, name: "Ravi", has_pin: true, employment_status: "active" }} />);
    expect(page.getByText("482910")).toBeTruthy();
    fireEvent.click(page.getByText("Done"));
    expect(page.queryByText("482910")).toBeNull();
    expect(page.getByText("Re-issue station PIN")).toBeTruthy();
  });

  it("is not offered to somebody without the right, nor for a leaver", () => {
    allowed = false;
    expect(render(<StationPin employee={{ id: 7, has_pin: true, employment_status: "active" }} />).container.textContent).toBe("");
    cleanup();
    allowed = true;
    expect(render(<StationPin employee={{ id: 7, has_pin: false, employment_status: "terminated" }} />).container.textContent).toBe("");
  });
});
