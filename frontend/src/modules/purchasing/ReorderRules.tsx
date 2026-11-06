import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "item_label", label: "Item" },
  { key: "warehouse_name", label: "Where" },
  { key: "minimum", label: "Minimum", kind: "quantity" },
  { key: "target", label: "Up to", kind: "quantity" },
  { key: "vendor_name", label: "Vendor" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function ReorderRules() {
  return (
    <ListView<Row>
      title="Reorder rules"
      noun={["reorder rule", "reorder rules"]}
      endpoint="/api/purchasing/reorder-rules/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/reorder-rules/${row.id}`}
      searchHint="Item"
      create={{ href: "/purchasing/reorder-rules/new", permission: "purchasing.add_reorderrule" }}
    />
  );
}
