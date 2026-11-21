import { RecordScreen } from "../../views/RecordScreen";

/** One LUT as the portal acknowledged it, under the registration in force (CGST Rule 96A). */
export default function LutForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/letters-of-undertaking/"
      back="/settings/luts"
      backLabel="Letters of undertaking"
      newTitle="New LUT"
      heading={(row) => `LUT ${String(row.financial_year ?? "")} · ${String(row.arn ?? "")}`}
      permissions={{ add: "accounting.add_letterofundertaking", change: "accounting.change_letterofundertaking",
        delete: "accounting.delete_letterofundertaking" }}
      fields={[
        { key: "arn", label: "ARN", hint: "The acknowledgement the portal gave the LUT" },
        { key: "financial_year", label: "Financial year", hint: "2026-27" },
        { key: "filed_on", label: "Filed on", kind: "date" },
        { key: "valid_from", label: "Valid from", kind: "date" },
        { key: "valid_to", label: "Valid to", kind: "date" },
      ]}
    />
  );
}
