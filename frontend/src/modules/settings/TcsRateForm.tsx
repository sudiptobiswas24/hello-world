import { RecordScreen } from "../../views/RecordScreen";
import { TCS_SECTION } from "../accounts/refs";

/** One rate from the day it applies. A changed figure waits for the accountant's confirmation again. */
export default function TcsRateForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/tcs-rates/"
      back="/settings/tcs-rates"
      backLabel="TCS rates"
      newTitle="New rate"
      heading={(row) => `${String(row.section_code ?? "")} from ${String(row.applies_from ?? "")}`}
      state={(row) => (row.confirmed_on ? { label: "Confirmed", tone: "done" } : { label: "To confirm", tone: "draft" })}
      permissions={{ add: "accounting.add_tcsrate", change: "accounting.change_tcsrate", delete: "accounting.delete_tcsrate" }}
      fields={[
        { key: "section", label: "Section", kind: "ref", ref: TCS_SECTION, show: (row) => String(row.section_code) },
        { key: "applies_from", label: "Applies from", kind: "date" },
        { key: "rate_percent", label: "Rate %", kind: "decimal", places: 4, hint: "Collected from a buyer whose PAN is on file" },
        { key: "no_pan_rate_percent", label: "Without PAN %", kind: "decimal", places: 4, hint: "Section 206CC" },
        { key: "law_section", label: "Section in law", hint: "206C(1) until 31 March 2026, 394(1) from 1 April 2026; printed on the invoice" },
        { key: "authority", label: "Authority", hint: "The section or notification it comes from" },
        { key: "confirmed_on", label: "Confirmed on", kind: "date", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Confirm", path: "confirm", permission: "accounting.confirm_tcsrate", when: (row) => !row.confirmed_on,
          done: "Confirmed: invoices collect at it from its day" },
      ]}
    />
  );
}
