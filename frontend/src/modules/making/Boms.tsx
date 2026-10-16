import { ListView, type Column } from "../../views/ListView";

interface Bom { id: number; item_label: string; version: number; name: string; quantity_produced: string; uom_code: string;
  routing_name: string; is_default: boolean; is_active: boolean; components: unknown[]; [key: string]: unknown }

const columns: Column<Bom>[] = [
  { key: "item_label", label: "Makes" },
  { key: "version", label: "Version", width: "6rem" },
  { key: "name", label: "Name" },
  { key: "quantity_produced", label: "Per batch", width: "10rem", render: (row) => `${row.quantity_produced} ${row.uom_code}` },
  { key: "routing_name", label: "Routing", width: "12rem" },
  { key: "components", label: "Inputs", width: "6rem", render: (row) => String(row.components.length) },
  { key: "is_default", label: "", width: "7rem", render: (row) => (!row.is_active ? "Inactive" : row.is_default ? "Default" : "") },
];

export default function Boms() {
  return (
    <ListView<Bom>
      title="Bills of materials"
      noun={["bill of materials", "bills of materials"]}
      endpoint="/api/manufacturing/boms/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/boms/${row.id}`}
      searchHint="Item or name"
      facets={[{ label: "Active", params: { is_active: "true" } }, { label: "Phantom", params: { is_phantom: "true" } }]}
      create={{ href: "/making/boms/new", permission: "manufacturing.add_billofmaterials" }}
    />
  );
}
