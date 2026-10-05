import { asPercent, minutesAsHours } from "../../lib/decimal";
import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown>;

const percent = (value: unknown) => asPercent(value as string | null);
const hours = (value: unknown) => minutesAsHours(value as string | null);

const PARAMS: ParamDef[] = [
  { key: "centre", label: "Work centre", kind: "ref", required: true,
    ref: { endpoint: "/api/manufacturing/work-centres/", permission: "manufacturing.view_workcentre",
      label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.id) } },
  { key: "by", label: "By", kind: "choice", required: true, initial: "machine",
    choices: [["machine", "Machine"], ["shift", "Shift"]] },
  { key: "start", label: "From", kind: "date", initial: "-7" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/**
 * Where a work centre's hours went: availability, speed and good output
 * for each machine or each shift, and their product. The ratios are
 * shown as ratios a person can check (ran minutes over planned), never
 * invented where nothing was measured.
 */
export default function Losses() {
  const columns: Column<Row>[] = [
    { key: "label", label: "Machine or shift", render: (row) => String(row.machine ?? row.shift_name ?? "Not attributed") },
    { key: "oee", label: "OEE", width: "6rem", render: (row) => percent(row.oee) },
    { key: "availability", label: "Availability", width: "8rem", render: (row) => percent(row.availability) },
    { key: "performance", label: "Speed", width: "6rem", render: (row) => percent(row.performance) },
    { key: "quality", label: "Good output", width: "8rem", render: (row) => percent(row.quality) },
    { key: "ran", label: "Ran (h)", width: "6rem", kind: "quantity", render: (row) => hours(row.ran_minutes) },
    { key: "unplanned", label: "Stopped, unplanned (h)", width: "10rem", kind: "quantity", render: (row) => hours(row.unplanned_stop_minutes) },
    { key: "planned", label: "Stopped, planned (h)", width: "9rem", kind: "quantity", render: (row) => hours(row.planned_stop_minutes) },
  ];
  return (
    <ReportView<Row[], Row>
      title="Machine effectiveness"
      endpoint={(values) => values.centre ? `/api/manufacturing/work-centres/${values.centre}/by-${values.by || "machine"}/` : null}
      params={PARAMS}
      send={(values) => ({ start: values.start, end: values.end })}
      rows={(data) => data}
      columns={columns}
      waiting="Choose a work centre."
      empty="Nothing ran in these days."
    />
  );
}
