import { RecordScreen } from "../../views/RecordScreen";
import { ITEM } from "./refs";

/** One batch: its dates and where it has been. Made by the receipt or the run that produced it. */
export default function LotForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/lots/"
      back="/stores/batches"
      backLabel="Batches"
      newTitle="New batch"
      heading={(row) => `${String(row.code)} · ${String(row.item_label)}`}
      state={(row) => (row.has_expired ? { label: "Expired", tone: "warn" } : null)}
      permissions={{ change: "inventory.change_lot" }}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: ITEM, createOnly: true, show: (row) => String(row.item_label) },
        { key: "code", label: "Batch", createOnly: true },
        { key: "supplier_reference", label: "Supplier's batch" },
        { key: "manufactured_on", label: "Made on", kind: "date" },
        { key: "expires_on", label: "Expires on", kind: "date" },
        { key: "is_active", label: "Active", kind: "bool" },
        { key: "notes", label: "Notes", kind: "textarea" },
        { key: "on_hand", label: "On hand", readOnly: true },
      ]}
      panels={[{
        title: "Where it has been", permission: "inventory.view_stockmovement",
        endpoint: "/api/inventory/stock-movements/", query: (record) => ({ lot: record.id, ordering: "-occurred_at" }),
        columns: [
          { key: "occurred_at", label: "When", kind: "date" },
          { key: "movement_type", label: "What", kind: "status" },
          { key: "warehouse_code", label: "Warehouse" },
          { key: "quantity", label: "Quantity", kind: "quantity" },
          { key: "reference", label: "Document" },
        ],
      }]}
    />
  );
}
