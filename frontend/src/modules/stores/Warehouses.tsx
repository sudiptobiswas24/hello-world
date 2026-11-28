import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "is_quarantine", label: "Quarantine" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function Warehouses() {
  return (
    <ListView<Row>
      title="Warehouses"
      noun={["warehouse", "warehouses"]}
      endpoint="/api/inventory/warehouses/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/warehouses/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/stores/warehouses/new", permission: "inventory.add_warehouse" }}
    />
  );
}
