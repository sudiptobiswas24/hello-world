import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "period_start", label: "From", kind: "date", sort: "period_start", width: "8rem" },
  { key: "period_end", label: "To", kind: "date", sort: "period_end", width: "8rem" },
  { key: "employee_name", label: "Who" },
  { key: "hours", label: "Hours", kind: "quantity", width: "7rem" },
  { key: "billable_hours", label: "Billable", kind: "quantity", width: "7rem" },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

/** Hours worked: one's own, one's reports' to decide, or everyone's to who keeps the records. */
export default function Timesheets() {
  return (
    <ListView<Row>
      title="Timesheets"
      noun={["timesheet", "timesheets"]}
      endpoint="/api/hr/timesheets/"
      columns={columns}
      rowHref={(row) => `/payroll/timesheets/${row.id}`}
      searchHint="Who or note"
      facets={[{ label: "To decide", params: { status: "submitted" } }, { label: "Approved", params: { status: "approved" } }]}
      create={{ href: "/payroll/timesheets/new", permission: "hr.add_timesheet" }}
    />
  );
}
