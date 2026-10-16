import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
];

export default function Departments() {
  return (
    <ListView<Row>
      title="Departments"
      noun={["department", "departments"]}
      endpoint="/api/hr/departments/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/departments/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/payroll/departments/new", permission: "hr.add_department" }}
    />
  );
}
