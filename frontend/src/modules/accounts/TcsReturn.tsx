import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { collection: number };

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", required: true, initial: "-90" },
  { key: "to", label: "To", kind: "date", required: true, initial: "today" },
];

/** The quarter's collections as the return (Form 143, was 27EQ) lists them, with the challan that paid each over. */
export default function TcsReturn() {
  return (
    <ReportView<Row[], Row>
      title="TCS return"
      endpoint="/api/sales/tcs-collections/quarter/"
      params={PARAMS}
      rows={(data) => data.map((row) => ({ ...row, id: row.collection }))}
      columns={[
        { key: "date", label: "Date", kind: "date", width: "8rem" },
        { key: "section", label: "Section", width: "6rem" },
        { key: "buyer", label: "Buyer" },
        { key: "pan", label: "PAN", width: "8rem" },
        { key: "base", label: "Collected on", kind: "money", width: "10rem" },
        { key: "amount", label: "Collected", kind: "money", width: "9rem" },
        { key: "not_collected", label: "Not collected", width: "7rem" },
        { key: "challan", label: "Challan", width: "10rem", render: (row) => String(row.challan || "Not paid over") },
      ]}
      empty="Nothing was collected in these dates."
    />
  );
}
