import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { collection: number };
/** The return as the API makes it: the TAN and the names standing at the period's end, above its rows. */
type Return = { tan: string; act: string; form: string; challan_form: string; rows: Row[] };

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", required: true, initial: "-90" },
  { key: "to", label: "To", kind: "date", required: true, initial: "today" },
];

/** The quarter's collections as the return lists them, with the challan that paid each over; the form is named by the API for the period. */
export default function TcsReturn() {
  return (
    <ReportView<Return, Row>
      title="TCS return"
      endpoint="/api/sales/tcs-collections/quarter/"
      params={PARAMS}
      above={(data) => <p className="muted">{data.form} under the {data.act}, TAN {data.tan}; paid over by {data.challan_form}</p>}
      rows={(data) => data.rows.map((row) => ({ ...row, id: row.collection }))}
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
