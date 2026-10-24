import { RecordScreen } from "../../views/RecordScreen";
import { ITEM } from "./refs";

type Row = Record<string, unknown> & { id: number };
type LotRow = { code: string; item: string };

/** What a batch was made from, every level back: for a complaint. */
function madeFrom(data: unknown): Row[] {
  return (data as { level: number; lot: LotRow; made_by: string; from_lot: LotRow; quantity: string }[]).map((row, index) => ({
    id: index, level: row.level, lot_code: row.lot.code, made_by: row.made_by,
    from_code: row.from_lot.code, from_item: row.from_lot.item, quantity: row.quantity,
  }));
}

/** Who was shipped it, or something made from it: for a recall. */
function heldBy(data: unknown): Row[] {
  const report = data as { customers: { customer: string; name: string; lot: LotRow; quantity: string; deliveries: string[] }[] };
  return report.customers.map((row, index) => ({
    id: index, name: `${row.customer} · ${row.name}`, lot_code: row.lot.code, quantity: row.quantity,
    deliveries: row.deliveries.join(", "),
  }));
}

/** One batch: its dates, what it was made from, who holds it and where it has been. */
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
        title: "Made from", permission: "inventory.view_lot", endpoint: "", query: () => ({}),
        read: { path: (record) => `/api/manufacturing/lot-trace/${String(record.id)}/made-from/`, rows: madeFrom },
        columns: [
          { key: "level", label: "Back", width: "5rem" },
          { key: "lot_code", label: "Batch" },
          { key: "made_by", label: "By run", width: "10rem" },
          { key: "from_code", label: "From batch" },
          { key: "from_item", label: "Of", width: "10rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
        ],
      }, {
        title: "Who holds it", permission: "inventory.view_lot", endpoint: "", query: () => ({}),
        read: { path: (record) => `/api/manufacturing/lot-trace/${String(record.id)}/recall/`, rows: heldBy },
        columns: [
          { key: "name", label: "Customer" },
          { key: "lot_code", label: "As batch", width: "10rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "deliveries", label: "Deliveries" },
        ],
      }, {
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
