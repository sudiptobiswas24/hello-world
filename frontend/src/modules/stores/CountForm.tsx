import { RecordScreen } from "../../views/RecordScreen";
import { BIN, ITEM, LOT, REASON, WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const counting = (row: Row) => !row.posted;

/**
 * A count sheet. Each line freezes what the books said the moment it was
 * added, so stock moving while the store is counted does not read as a
 * loss; posting books the differences as one adjustment.
 */
export default function CountForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/stock-counts/"
      back="/stores/counts"
      backLabel="Stock counts"
      newTitle="New stock count"
      heading={(row) => String(row.number || "Count")}
      state={(row) => (row.posted ? { label: "Posted", tone: "done" } : { label: "Counting", tone: "open" })}
      permissions={{ add: "inventory.add_stockcount", change: "inventory.change_stockcount", delete: "inventory.delete_stockcount" }}
      editable={counting}
      fields={[
        { key: "count_date", label: "Counted on", kind: "date" },
        { key: "warehouse", label: "Warehouse", kind: "ref", ref: WAREHOUSE, createOnly: true },
        { key: "reason", label: "Differences booked as", kind: "ref", ref: REASON },
        { key: "counted_by", label: "Counted by", kind: "text" },
        { key: "memo", label: "Memo", kind: "text", wide: true },
      ]}
      actions={[{ label: "Post the differences", path: "post", permission: "inventory.change_stockcount",
        when: counting, primary: true, done: "Posted", fields: [{ key: "memo", label: "Memo", kind: "text" }] }]}
      panels={[{
        title: "Counted", permission: "inventory.view_stockcount", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "lot_code", label: "Batch", width: "9rem" },
          { key: "bin_code", label: "Bin", width: "7rem" },
          { key: "system_quantity", label: "Books said", kind: "quantity", width: "9rem" },
          { key: "counted_quantity", label: "Counted", kind: "quantity", width: "9rem" },
          { key: "variance", label: "Difference", kind: "quantity", width: "9rem" },
        ],
        adder: { label: "Add a count", permission: "inventory.add_stockcountline", when: counting,
          url: (record) => `/api/inventory/stock-counts/${record.id}/add/`,
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "counted_quantity", label: "Counted", kind: "decimal" },
            { key: "lot", label: "Batch", kind: "pick", pick: LOT, hint: "For an item tracked by batch" },
            { key: "bin", label: "Bin", kind: "ref", ref: BIN },
          ],
          body: (values) => values },
        remover: { permission: "inventory.delete_stockcountline", when: counting,
          url: (row) => `/api/inventory/stock-count-lines/${row.id}/` },
      }]}
    />
  );
}
