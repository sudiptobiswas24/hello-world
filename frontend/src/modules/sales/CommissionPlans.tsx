import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "percent", label: "%", kind: "quantity" },
  { key: "basis", label: "Basis" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function CommissionPlans() {
  return (
    <ListView<Row>
      title="Commission plans"
      noun={["commission plan", "commission plans"]}
      endpoint="/api/sales/commission-plans/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/commission-plans/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/sales/commission-plans/new", permission: "sales.add_commissionplan" }}
    />
  );
}
