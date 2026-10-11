import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { deduction: number };

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", required: true, initial: "-90" },
  { key: "to", label: "To", kind: "date", required: true, initial: "today" },
];

/** The quarter's deductions as the return (26Q) lists them, with the challan that paid each over. */
export default function TdsReturn() {
  return (
    <ReportView<Row[], Row>
      title="TDS return"
      endpoint="/api/purchasing/tds-deductions/quarter/"
      params={PARAMS}
      rows={(data) => data.map((row) => ({ ...row, id: row.deduction }))}
      columns={[
        { key: "date", label: "Date", kind: "date", width: "8rem" },
        { key: "section", label: "Section", width: "6rem" },
        { key: "vendor", label: "Deductee" },
        { key: "pan", label: "PAN", width: "8rem" },
        { key: "base", label: "Credited", kind: "money", width: "10rem" },
        { key: "amount", label: "Deducted", kind: "money", width: "9rem" },
        { key: "challan", label: "Challan", width: "10rem", render: (row) => String(row.challan || "Not paid over") },
      ]}
      empty="Nothing was deducted in these dates."
    />
  );
}
