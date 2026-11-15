import { describe, expect, it } from "vitest";

import { blank, bodyFor, onNewSheet, shown } from "../../views/RecordScreen";
import { LEAD_FIELDS } from "./LeadForm";

describe("a new lead (O588)", () => {
  const status = LEAD_FIELDS.find((field) => field.key === "status")!;

  it("shows its status as New before it is saved", () => {
    const fresh = blank(LEAD_FIELDS);
    expect(onNewSheet(status)).toBe(true);
    expect(shown(status, fresh, undefined)).toBe("New");
  });

  it("never sends the status, which the server keeps (O565)", () => {
    const body = bodyFor(LEAD_FIELDS, { ...blank(LEAD_FIELDS), company_name: "Konkan Agro" }, true);
    expect("status" in body).toBe(false);
    expect(body.company_name).toBe("Konkan Agro");
  });

  it("still leaves off a new lead what only a saved one has", () => {
    const became = LEAD_FIELDS.find((field) => field.key === "converted_party_name")!;
    expect(onNewSheet(became)).toBe(false);
    expect("converted_party_name" in blank(LEAD_FIELDS)).toBe(false);
  });
});
