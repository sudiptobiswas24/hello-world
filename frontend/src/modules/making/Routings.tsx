import { ListView, type Column } from "../../views/ListView";

interface Routing { id: number; code: string; name: string; is_active: boolean; operations: unknown[]; [key: string]: unknown }

const columns: Column<Routing>[] = [
  { key: "code", label: "Routing", sort: "code", width: "10rem" },
  { key: "name", label: "Name" },
  { key: "operations", label: "Steps", width: "6rem", render: (row) => String(row.operations.length) },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function Routings() {
  return (
    <ListView<Routing>
      title="Routings"
      noun={["routing", "routings"]}
      endpoint="/api/manufacturing/routings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/routings/${row.id}`}
      searchHint="Code or name"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/making/routings/new", permission: "manufacturing.add_routing" }}
    />
  );
}
