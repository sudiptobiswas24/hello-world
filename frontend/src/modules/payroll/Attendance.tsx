import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "on", label: "Day", kind: "date", sort: "on", width: "8rem" },
  { key: "employee_number", label: "No.", sort: "employee__employee_number", width: "7rem" },
  { key: "employee_name", label: "Who" },
  { key: "status", label: "", kind: "status", width: "7rem" },
  { key: "shift", label: "Shift", width: "6rem" },
  { key: "time_in", label: "In", width: "6rem" },
  { key: "time_out", label: "Out", width: "6rem" },
  // A count of minutes, not money: a whole number from the server.
  { key: "late_minutes", label: "Late", width: "6rem", render: (row) => (Number(row.late_minutes) ? `${String(row.late_minutes)} min` : "") },
  { key: "overtime_hours", label: "Overtime h", kind: "quantity", width: "8rem" },
];

/** The shift register: who came to which shift, half days and absences, how late, and the overtime agreed. */
export default function Attendance() {
  return (
    <ListView<Row>
      title="Attendance"
      noun={["day", "days"]}
      endpoint="/api/hr/attendance/"
      columns={columns}
      facets={[
        { label: "Absent", params: { status: "absent" } },
        { label: "Half days", params: { status: "half_day" } },
        { label: "Entered by hand", params: { source: "manual" } },
      ]}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/attendance/${row.id}`}
      searchHint="Employee number or name"
      create={{ href: "/payroll/attendance/new", permission: "hr.add_attendanceday" }}
    />
  );
}
