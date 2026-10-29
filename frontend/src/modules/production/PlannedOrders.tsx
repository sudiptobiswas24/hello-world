import { ListView, type Column } from "../../views/ListView";

interface PlannedOrder {
  id: number;
  run: number;
  item_sku: string;
  item_name: string;
  kind: string;
  quantity: string;
  needed_by: string;
  release_on: string;
  status: string;
  work_order_number: string;
  vendor_name: string;
}

const KIND: Record<string, string> = { make: "Make", buy: "Buy", transfer: "Move" };

const columns: Column<PlannedOrder>[] = [
  { key: "item_sku", label: "Item", width: "10rem" },
  { key: "item_name", label: "" },
  { key: "kind", label: "How", width: "6rem", render: (row) => KIND[row.kind] ?? row.kind },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
  { key: "release_on", label: "Start by", kind: "date", sort: "release_on", width: "8rem" },
  { key: "needed_by", label: "Wanted", kind: "date", sort: "needed_by", width: "8rem" },
  { key: "work_order_number", label: "Became", width: "10rem", render: (row) => row.work_order_number || row.vendor_name || "" },
  { key: "status", label: "State", kind: "status", width: "7rem" },
];

/** Every suggestion every run made, to find one again. */
export default function PlannedOrders() {
  return (
    <ListView<PlannedOrder>
      title="Planned orders"
      noun={["planned order", "planned orders"]}
      endpoint="/api/planning/planned-orders/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/plan/${row.run}`}
      searchHint="Item code or name"
      facets={[
        { label: "To decide", params: { status: "suggested" } },
        { label: "Firmed", params: { status: "firmed" } },
        { label: "Make", params: { kind: "make" } },
        { label: "Buy", params: { kind: "buy" } },
      ]}
    />
  );
}
