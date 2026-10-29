import { minutesAsHours } from "../../lib/decimal";
import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { reason: string; name: string; planned: boolean; stoppages: number; minutes: string; share: string; [key: string]: unknown }
interface Data { rows: Row[]; total_minutes: string }

const PARAMS: ParamDef[] = [
  { key: "work_centre", label: "Work centre", kind: "ref",
    ref: { endpoint: "/api/manufacturing/work-centres/", permission: "manufacturing.view_workcentre",
      label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.code) } },
  { key: "start", label: "From", kind: "date", initial: "-30" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/** Stoppages by reason, the costliest first: where to look before buying a loom. */
export default function HoursLost() {
  const columns: Column<Row>[] = [
    { key: "name", label: "Reason" },
    { key: "planned", label: "Planned", width: "6rem", render: (row) => (row.planned ? "Yes" : "No") },
    { key: "stoppages", label: "Stoppages", width: "7rem", kind: "quantity", render: (row) => String(row.stoppages) },
    { key: "hours", label: "Hours", width: "6rem", kind: "quantity", render: (row) => minutesAsHours(row.minutes) },
    { key: "share", label: "Share", width: "6rem", render: (row) => `${row.share}%` },
  ];
  return (
    <ReportView<Data, Row>
      title="Where the hours went"
      endpoint="/api/manufacturing/downtime/by-reason/"
      params={PARAMS}
      rows={(data) => data.rows}
      columns={columns}
      foot={(_rows, data) => ["Total", null, null, minutesAsHours(data.total_minutes), null]}
      empty="No stoppages in these days."
    />
  );
}
