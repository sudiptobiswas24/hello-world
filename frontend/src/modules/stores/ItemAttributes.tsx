import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function ItemAttributes() {
  return (
    <ListView<Row>
      title="Item attributes"
      noun={["item attribute", "item attributes"]}
      endpoint="/api/inventory/item-attributes/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/attributes/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/stores/attributes/new", permission: "inventory.add_itemattribute" }}
    />
  );
}
