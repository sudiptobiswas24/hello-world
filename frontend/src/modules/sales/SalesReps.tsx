import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "name", label: "Rep" },
  { key: "plan_name", label: "Commission plan" },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function SalesReps() {
  return (
    <ListView<Row>
      title="Sales reps"
      noun={["sales rep", "sales reps"]}
      endpoint="/api/sales/sales-reps/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/reps/${row.id}`}
      searchHint="Code or name"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/sales/reps/new", permission: "sales.add_salesrep" }}
    />
  );
}
