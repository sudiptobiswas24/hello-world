import { RecordScreen } from "../../views/RecordScreen";
import { SECTION } from "../accounts/refs";

/** One rate from the day it applies. A changed figure waits for the accountant's confirmation again. */
export default function TdsRateForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tds-rates/"
      back="/settings/tds-rates"
      backLabel="TDS rates"
      newTitle="New rate"
      heading={(row) => `${String(row.section_code ?? "")} from ${String(row.applies_from ?? "")}`}
      state={(row) => (row.confirmed_on ? { label: "Confirmed", tone: "done" } : { label: "To confirm", tone: "draft" })}
      permissions={{ add: "accounting.add_tdsrate", change: "accounting.change_tdsrate", delete: "accounting.delete_tdsrate" }}
      fields={[
        { key: "section", label: "Section", kind: "ref", ref: SECTION, show: (row) => String(row.section_code) },
        { key: "applies_from", label: "Applies from", kind: "date" },
        { key: "rate_percent", label: "Rate %", kind: "decimal", places: 4, hint: "Deducted from a party whose PAN is on file" },
        { key: "no_pan_rate_percent", label: "Without PAN %", kind: "decimal", places: 4, hint: "Section 206AA" },
        { key: "authority", label: "Authority", hint: "The section or notification it comes from" },
        { key: "confirmed_on", label: "Confirmed on", kind: "date", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Confirm", path: "confirm", permission: "accounting.confirm_tdsrate", when: (row) => !row.confirmed_on,
          done: "Confirmed: bills are deducted at it from its day" },
      ]}
    />
  );
}
