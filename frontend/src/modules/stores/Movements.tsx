import { dateTime, quantity } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Movement {
  id: number;
  item_sku: string;
  item_name: string;
  warehouse_code: string;
  movement_type: string;
  lot_code: string;
  quantity: string;
  reference: string;
  occurred_at: string;
}

const KIND: Record<string, string> = {
  receipt: "In", issue: "Out", adjustment: "Adjusted", transfer_in: "Moved in", transfer_out: "Moved out",
};

const columns: Column<Movement>[] = [
  { key: "occurred_at", label: "When", sort: "occurred_at", width: "11rem", render: (row) => dateTime(row.occurred_at) },
  { key: "item_sku", label: "Item", width: "9rem" },
  { key: "item_name", label: "" },
  { key: "warehouse_code", label: "Where", width: "6rem" },
  { key: "movement_type", label: "What", width: "8rem", render: (row) => KIND[row.movement_type] ?? row.movement_type.replace(/_/g, " ") },
  { key: "lot_code", label: "Batch", width: "9rem" },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem", render: (row) => quantity(row.quantity) },
  { key: "reference", label: "Reference", width: "10rem" },
];

/** The stock ledger: every movement, as the documents that made it wrote it. */
export default function Movements() {
  return (
    <ListView<Movement>
      title="Stock movements"
      noun={["movement", "movements"]}
      endpoint="/api/inventory/stock-movements/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Item code or name, batch, reference"
    />
  );
}
