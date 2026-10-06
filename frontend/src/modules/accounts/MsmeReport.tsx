import { date } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { bill: number };

const PARAMS: ParamDef[] = [
  { key: "start", label: "Bills from", kind: "date", required: true, initial: "-90" },
  { key: "end", label: "To", kind: "date", required: true, initial: "today" },
  { key: "as_of", label: "As of", kind: "date", required: true, initial: "today" },
];

/**
 * Micro and small vendors' bills against the MSME Act's days: the agreed
 * days, never more than 45, or 15 with none agreed. What stands unpaid
 * past its date at the year's end is added back to profit (43B(h)).
 */
export default function MsmeReport() {
  return (
    <ReportView<Row[], Row>
      title="MSME payments"
      endpoint="/api/purchasing/purchasing-reports/msme/"
      params={PARAMS}
      rows={(data) => data.map((row) => ({ ...row, id: row.bill }))}
      columns={[
        { key: "number", label: "Bill", width: "9rem" },
        { key: "vendor", label: "Vendor" },
        { key: "category", label: "", width: "5rem" },
        { key: "bill_date", label: "Dated", kind: "date", width: "8rem" },
        { key: "due", label: "Due by the Act", kind: "date", width: "9rem" },
        { key: "paid_on", label: "Paid", width: "8rem", render: (row) => (row.paid_on ? date(String(row.paid_on)) : "Not yet") },
        { key: "days_late", label: "Days late", width: "6rem" },
        { key: "unpaid", label: "Unpaid", kind: "money", width: "9rem" },
        { key: "at_risk", label: "At risk", kind: "money", width: "9rem" },
      ]}
      empty="No micro or small vendor billed in these dates."
    />
  );
}
