import { ListView, type Column } from "../../views/ListView";

interface Bin { id: number; code: string; name: string; warehouse_name: string; is_pickable: boolean; is_active: boolean; [key: string]: unknown }

const columns: Column<Bin>[] = [
  { key: "code", label: "Bin", width: "10rem", sort: "code" },
  { key: "name", label: "Name" },
  { key: "warehouse_name", label: "Warehouse" },
  { key: "is_pickable", label: "Picked from", width: "8rem", render: (row) => (row.is_pickable ? "Yes" : "No") },
];

export default function Bins() {
  return (
    <ListView<Bin>
      title="Bins"
      noun={["bin", "bins"]}
      endpoint="/api/inventory/bins/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/bins/${row.id}`}
      searchHint="Bin, name"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/stores/bins/new", permission: "inventory.add_storagebin" }}
    />
  );
}
