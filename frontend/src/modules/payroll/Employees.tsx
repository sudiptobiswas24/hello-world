import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "employee_number", label: "Employee number" },
  { key: "name", label: "Name" },
  { key: "job_title", label: "Job title" },
  { key: "employment_status", label: "Employment status" },
];

export default function Employees() {
  return (
    <ListView<Row>
      title="Employees"
      noun={["employee", "employees"]}
      endpoint="/api/hr/employees/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/employees/${row.id}`}
      searchHint="Number or name"
      create={{ href: "/payroll/employees/new", permission: "hr.add_employee" }}
    />
  );
}
