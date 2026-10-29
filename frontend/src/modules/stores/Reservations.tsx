import { quantity } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Reservation { id: number; item_label: string; warehouse_name: string; quantity: string; remaining: string; held_for: string; released_at: string | null; [key: string]: unknown }

const columns: Column<Reservation>[] = [
  { key: "item_label", label: "Item" },
  { key: "warehouse_name", label: "Warehouse", width: "11rem" },
  { key: "held_for", label: "Held for" },
  { key: "quantity", label: "Held", kind: "quantity", width: "8rem", render: (row) => quantity(row.quantity) },
  { key: "remaining", label: "Still held", kind: "quantity", width: "8rem", render: (row) => quantity(row.remaining) },
];

/** Stock promised to orders and runs: made and released by the documents that hold it, never here. */
export default function Reservations() {
  return (
    <ListView<Reservation>
      title="Reserved stock"
      noun={["reservation", "reservations"]}
      endpoint="/api/inventory/stock-reservations/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Item"
      facets={[{ label: "Still held", params: { released_at__isnull: "true" } }]}
    />
  );
}
