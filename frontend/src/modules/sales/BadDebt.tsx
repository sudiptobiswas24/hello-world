import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { customer: string; written_off: string; recovered: string; net: string; [key: string]: unknown }

const PARAMS: ParamDef[] = [
  { key: "start", label: "From", kind: "date", initial: "-365" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/** Receivables written off in a span, by customer, less what was later recovered. */
export default function BadDebt() {
  const columns: Column<Row>[] = [
    { key: "customer", label: "Customer" },
    { key: "written_off", label: "Written off", kind: "money", width: "10rem" },
    { key: "recovered", label: "Recovered", kind: "money", width: "10rem" },
    { key: "net", label: "Lost", kind: "money", width: "10rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Bad debts"
      endpoint="/api/sales/sales-reports/bad-debt/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="Nothing written off in these days."
    />
  );
}
