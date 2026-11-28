import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChangeList } from "./HistoryPanel";

afterEach(cleanup);

describe("a change in History (the sweep of the demo plant, g52)", () => {
  it("folds a record kept whole, and shows plain values as they came", () => {
    const { container } = render(
      <ChangeList
        changes={[
          { field: "issued_as", label: "issued as", from: null, to: { supplier: { gstin: "27AAECD4821K1ZL" }, delivery: null } },
          { field: "number", label: "number", from: "", to: "CN-2026-00001" },
        ]}
      />,
    );
    expect(container.querySelector("details.kept summary")?.textContent).toBe("2 details kept");
    expect(container.querySelector("details.kept pre")?.textContent).toContain('"gstin": "27AAECD4821K1ZL"');
    expect(container.textContent).toContain("number: nothing → CN-2026-00001");
    expect(container.textContent).not.toContain('{"supplier"');
  });
});
