import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Job {
  id: number;
  due_on: string;
  serves: string;
  title: string;
  is_breakdown: boolean;
  status: string;
  technician_name: string;
  [key: string]: unknown;
}

const columns: Column<Job>[] = [
  { key: "due_on", label: "Due", width: "8rem", sort: "due_on", render: (row) => date(row.due_on) },
  { key: "serves", label: "Where" },
  { key: "title", label: "What" },
  { key: "kind", label: "Kind", width: "8rem", render: (row) => (row.is_breakdown ? "Breakdown" : "Service") },
  { key: "technician_name", label: "Fitter", width: "10rem" },
  { key: "status", label: "State", width: "7rem", kind: "status" },
];

/** Maintenance work, open first: services that fell due and repairs raised on stoppages. */
export default function Jobs() {
  return (
    <ListView<Job>
      title="Maintenance jobs"
      noun={["job", "jobs"]}
      endpoint="/api/manufacturing/maintenance-jobs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/plant/jobs/${row.id}`}
      searchHint="Fault, notes, schedule"
      facets={[
        { label: "Open", params: { done_on__isnull: "true", cancelled_at__isnull: "true" } },
        { label: "Breakdowns", params: { is_breakdown: "true" } },
      ]}
      create={{ href: "/plant/jobs/new", permission: "manufacturing.add_maintenancejob" }}
    />
  );
}
