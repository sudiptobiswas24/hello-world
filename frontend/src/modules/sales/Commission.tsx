import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { rep: string; plan: string; basis: string; basis_amount: string; percent: string; commission: string; [key: string]: unknown }

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", initial: "month-start" },
  { key: "to", label: "To", kind: "date", initial: "today" },
];

/** Commission earned by each rep over a span, each on their own plan's basis: what they billed, or what came in. */
export default function Commission() {
  const columns: Column<Row>[] = [
    { key: "rep", label: "Rep" },
    { key: "plan", label: "Plan", width: "8rem" },
    { key: "basis", label: "On", width: "8rem", render: (row) => (row.basis === "paid" ? "Collected" : "Invoiced") },
    { key: "basis_amount", label: "Basis", kind: "money", width: "10rem" },
    { key: "percent", label: "%", kind: "quantity", width: "6rem" },
    { key: "commission", label: "Commission", kind: "money", width: "10rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Commission"
      endpoint="/api/sales/commission-plans/report/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="No commission earned in these days."
    />
  );
}
