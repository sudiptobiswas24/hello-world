import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row {
  id: number; kind: string; who: string; period_start: string; period_end: string;
  target: string; actual: string; percent: string | null; shortfall: string; [key: string]: unknown;
}

const PARAMS: ParamDef[] = [
  { key: "from", label: "From", kind: "date", initial: "month-start" },
  { key: "to", label: "To", kind: "date", initial: "today" },
];

/** Each target touching the span against what its team or rep posted in the target's own span. */
export default function TargetsReport() {
  const columns: Column<Row>[] = [
    { key: "who", label: "Team or rep", render: (row) => `${row.who}${row.kind === "team" ? " (team)" : ""}` },
    { key: "period_start", label: "From", kind: "date", width: "9rem" },
    { key: "period_end", label: "To", kind: "date", width: "9rem" },
    { key: "target", label: "Target", kind: "money", width: "10rem" },
    { key: "actual", label: "Posted", kind: "money", width: "10rem" },
    { key: "percent", label: "%", kind: "quantity", width: "6rem", render: (row) => (row.percent === null ? "—" : row.percent) },
    { key: "shortfall", label: "Short by", kind: "money", width: "10rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Targets against sales"
      endpoint="/api/sales/sales-targets/report/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="No target touches these days."
    />
  );
}
