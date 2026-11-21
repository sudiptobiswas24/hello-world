import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "kind", label: "Kind" },
  { key: "basis", label: "Basis" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function PayComponents() {
  return (
    <ListView<Row>
      title="Pay components"
      noun={["pay component", "pay components"]}
      endpoint="/api/hr/pay-components/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/pay-components/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/payroll/pay-components/new", permission: "hr.add_paycomponent" }}
    />
  );
}
