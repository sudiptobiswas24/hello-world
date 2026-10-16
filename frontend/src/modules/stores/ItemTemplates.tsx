import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "variant_count", label: "Variants", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function ItemTemplates() {
  return (
    <ListView<Row>
      title="Item templates"
      noun={["item template", "item templates"]}
      endpoint="/api/inventory/item-templates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/item-templates/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/stores/item-templates/new", permission: "inventory.add_itemtemplate" }}
    />
  );
}
