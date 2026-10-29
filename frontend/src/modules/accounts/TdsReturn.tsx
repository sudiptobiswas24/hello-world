import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { deduction: number };
/** The return as the API makes it: the TAN and the names standing at the period's end, above its rows. */
type Return = { tan: string; act: string; form: string; challan_form: string; rows: Row[] };

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", required: true, initial: "-90" },
  { key: "to", label: "To", kind: "date", required: true, initial: "today" },
];

/** The quarter's deductions as the return lists them, with the challan that paid each over; the form is named by the API for the period. */
export default function TdsReturn() {
  return (
    <ReportView<Return, Row>
      title="TDS return"
      endpoint="/api/purchasing/tds-deductions/quarter/"
      params={PARAMS}
      above={(data) => <p className="muted">{data.form} under the {data.act}, TAN {data.tan}; paid over by {data.challan_form}</p>}
      rows={(data) => data.rows.map((row) => ({ ...row, id: row.deduction }))}
      columns={[
        { key: "date", label: "Date", kind: "date", width: "8rem" },
        { key: "section", label: "Section", width: "6rem" },
        { key: "vendor", label: "Deductee" },
        { key: "pan", label: "PAN", width: "8rem" },
        { key: "base", label: "Credited", kind: "money", width: "10rem" },
        { key: "amount", label: "Deducted", kind: "money", width: "9rem" },
        { key: "challan", label: "Challan", width: "10rem", render: (row) => String(row.challan || "Not paid over") },
        // A reversal row gives back a deduction: the day of the one it reverses (O156).
        { key: "reverses", label: "Reverses", kind: "date", width: "8rem" },
      ]}
      empty="Nothing was deducted in these dates."
    />
  );
}
