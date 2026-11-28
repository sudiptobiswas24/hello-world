import { Link } from "react-router";

import { RecordScreen } from "../../views/RecordScreen";

/** A row at a certificate's nil rate: nothing deducted, so nothing to pay over (O618). */
const atNil = (row: Record<string, unknown>) => /^0(\.0+)?$/.test(String(row.amount ?? ""));

/** One deduction from a bill. Not changed: reversed, until a challan has paid it over. */
export default function TdsDeductionForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/tds-deductions/"
      back="/accounts/tds-deducted"
      backLabel="TDS deducted"
      newTitle="Deduction"
      heading={(row) => `${String(row.section_code ?? "")} on ${String(row.bill_number ?? "")}`}
      state={(row) => (row.reversed ? { label: "Reversed", tone: "void" } : row.challan_label ? { label: "Paid over", tone: "done" }
        : atNil(row) ? { label: "At nil, nothing to pay over", tone: "done" } : null)}
      permissions={{}}
      fields={[
        { key: "bill", label: "Bill", readOnly: true, show: (row) => <Link to={`/purchasing/bills/${String(row.bill)}`}>{String(row.bill_number)}</Link> },
        { key: "vendor_name", label: "Vendor", readOnly: true },
        { key: "pan", label: "PAN", readOnly: true, show: (row) => String(row.pan || "Not on file") },
        { key: "base", label: "On", kind: "money", readOnly: true },
        { key: "rate_percent", label: "Rate %", readOnly: true },
        { key: "certificate", label: "Certificate", readOnly: true, show: (row) => String(row.certificate || "None: the section's rate") },
        { key: "amount", label: "Deducted", kind: "money", readOnly: true },
        { key: "date", label: "On the", kind: "date", readOnly: true },
        { key: "challan_label", label: "Challan", readOnly: true },
      ]}
      actions={[
        { label: "Reverse", path: "reverse", permission: "purchasing.post_bill", danger: true,
          when: (row) => !row.reversed && !row.challan_label, done: "Reversed: taken off the bill and the return" },
      ]}
    />
  );
}
