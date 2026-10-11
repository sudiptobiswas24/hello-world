import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "order_number", label: "Order", width: "10rem" },
  { key: "item_label", label: "They send" },
];

export default function SuppliedItems() {
  return (
    <ListView<Row>
      title="Material the customer sends"
      noun={["supplied item", "supplied items"]}
      endpoint="/api/sales/supplied-items/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/supplied-items/${row.id}`}
      searchHint="Order or item"
      create={{ href: "/sales/supplied-items/new", permission: "sales.add_supplieditem" }}
    />
  );
}
