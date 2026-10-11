import { minutesAsHours } from "../../lib/decimal";
import { money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Data {
  failures: number; repaired: number; run_hours: string; mtbf_hours: string | null;
  mttr_minutes: string | null; labour_minutes: string; spares_value: string;
}
type Row = { label: string; value: string; [key: string]: unknown };

const PARAMS: ParamDef[] = [
  { key: "machine", label: "Machine", kind: "ref", required: true,
    ref: { endpoint: "/api/manufacturing/machines/", permission: "manufacturing.view_machine",
      label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.code) } },
  { key: "start", label: "From", kind: "date", initial: "-90" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/** How often a machine fails, how long a repair takes, and what it cost. */
export default function Reliability() {
  return (
    <ReportView<Data, Row>
      title="Breakdowns"
      endpoint="/api/manufacturing/maintenance-jobs/reliability/"
      params={PARAMS}
      rows={(data) => [
        { label: "Breakdowns", value: String(data.failures) },
        { label: "Repaired", value: String(data.repaired) },
        { label: "Hours run", value: data.run_hours },
        { label: "Hours run between breakdowns", value: data.mtbf_hours ?? "—" },
        { label: "Hours a repair took", value: data.mttr_minutes ? minutesAsHours(data.mttr_minutes) : "—" },
        { label: "Fitters' hours", value: minutesAsHours(data.labour_minutes) },
        { label: "Spares", value: money(data.spares_value) },
      ]}
      columns={[{ key: "label", label: "" }, { key: "value", label: "", kind: "quantity" }]}
      waiting="Choose a machine."
    />
  );
}
