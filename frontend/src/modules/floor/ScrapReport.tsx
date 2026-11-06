import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { item: string; operation: string; reason: string; quantity: string; [key: string]: unknown }

const PARAMS: ParamDef[] = [
  { key: "start", label: "From", kind: "date", initial: "-30" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/** Scrap posted in a span, by what was being made, at which step and why. Unexplained is scrap nobody gave a reason for. */
export default function ScrapReport() {
  const columns: Column<Row>[] = [
    { key: "item", label: "Making", width: "12rem" },
    { key: "operation", label: "At step" },
    { key: "reason", label: "Why", width: "12rem" },
    { key: "quantity", label: "Scrap", kind: "quantity", width: "9rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Scrap by reason"
      endpoint="/api/manufacturing/run-flow/scrap/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="No scrap posted in these days."
    />
  );
}
