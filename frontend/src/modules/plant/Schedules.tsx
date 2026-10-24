import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Schedule {
  id: number;
  name: string;
  serves: string;
  every_days: number | null;
  every_run_hours: string | null;
  last_done_on: string | null;
  due_on: string | null;
  is_due: boolean;
  is_active: boolean;
  [key: string]: unknown;
}

const every = (row: Schedule) => [row.every_days ? `${row.every_days} days` : "", row.every_run_hours ? `${row.every_run_hours} running hours` : ""]
  .filter(Boolean).join(" or ");

const columns: Column<Schedule>[] = [
  { key: "name", label: "Work", sort: "name" },
  { key: "serves", label: "Where" },
  { key: "every", label: "Every", render: every },
  { key: "last_done_on", label: "Last done", width: "8rem", render: (row) => date(row.last_done_on) },
  { key: "due_on", label: "Due", width: "8rem", render: (row) => (row.is_due ? "Now" : date(row.due_on)) },
];

/** Routine work by the calendar or by running hours, whichever comes first. */
export default function Schedules() {
  return (
    <ListView<Schedule>
      title="Maintenance schedules"
      noun={["schedule", "schedules"]}
      endpoint="/api/manufacturing/maintenance-schedules/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/plant/schedules/${row.id}`}
      searchHint="Work"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/plant/schedules/new", permission: "manufacturing.add_maintenanceschedule" }}
    />
  );
}
