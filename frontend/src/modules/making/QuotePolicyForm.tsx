import { RecordScreen } from "../../views/RecordScreen";

/** The overhead and margin a quotation adds to cost, from a date. */
export default function QuotePolicyForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/quote-policies/"
      back="/making/quote-policies"
      backLabel="Quotation policy"
      newTitle="New quotation policy"
      heading={(row) => String(row.valid_from ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "manufacturing.add_quotepolicy", change: "manufacturing.change_quotepolicy", delete: "manufacturing.delete_quotepolicy" }}
      fields={[
        { key: "overhead_percent", label: "Overhead %", kind: "decimal", places: 2, hint: "On material and conversion together" },
        { key: "margin_percent", label: "Margin %", kind: "decimal", places: 2, hint: "On cost, unless a cost sheet is given its own" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "note", label: "Note" },
      ]}
    />
  );
}
