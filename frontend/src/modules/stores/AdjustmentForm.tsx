import { money } from "../../lib/format";
import { RecordScreen } from "../../views/RecordScreen";
import { ITEM, LOT, REASON, WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => !row.posted;

/**
 * Stock on or off the books, with the reason saying which account takes
 * the value. Posted, it is corrected by voiding it, which reverses both
 * the stock and the entry.
 */
export default function AdjustmentForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/stock-adjustments/"
      back="/stores/adjustments"
      backLabel="Adjustments"
      newTitle="New adjustment"
      heading={(row) => String(row.number || "Adjustment")}
      state={(row) => (row.voided ? { label: "Voided", tone: "draft" } : row.posted ? { label: "Posted", tone: "done" } : { label: "Draft", tone: "open" })}
      permissions={{ add: "inventory.add_stockadjustment", change: "inventory.change_stockadjustment", delete: "inventory.delete_stockadjustment" }}
      editable={draft}
      fields={[
        { key: "adjustment_date", label: "Date", kind: "date" },
        { key: "warehouse", label: "Warehouse", kind: "ref", ref: WAREHOUSE },
        { key: "reason", label: "Reason", kind: "ref", ref: REASON },
        { key: "memo", label: "Memo", kind: "text", wide: true },
        { key: "total_value", label: "Value", readOnly: true, show: (row) => money(row.total_value as string) },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "inventory.change_stockadjustment", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "inventory.change_stockadjustment", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided, done: "Voided",
          fields: [{ key: "memo", label: "Why", kind: "text" }, { key: "on_date", label: "On", kind: "date" }] },
      ]}
      panels={[{
        title: "Lines", permission: "inventory.view_stockadjustment", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "lot_code", label: "Batch", width: "9rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "unit_cost", label: "At", kind: "money", width: "9rem" },
          { key: "notes", label: "Notes" },
        ],
        adder: { label: "Add a line", permission: "inventory.add_stockadjustmentline", when: draft,
          url: () => "/api/inventory/stock-adjustment-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "quantity", label: "Quantity", kind: "decimal", negative: true, hint: "Less than nothing to write off" },
            { key: "lot", label: "Batch", kind: "pick", pick: LOT },
            { key: "notes", label: "Notes", kind: "text" },
          ],
          body: (values, record) => ({ adjustment: record.id, item: values.item, quantity: values.quantity,
            lot: values.lot, notes: values.notes }) },
        remover: { permission: "inventory.delete_stockadjustmentline", when: draft,
          url: (row) => `/api/inventory/stock-adjustment-lines/${row.id}/` },
      }]}
    />
  );
}
