import { RecordScreen } from "../../views/RecordScreen";
import { ITEM, LOT, WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => row.status === "draft";

/**
 * A transfer: lines while a draft; then posted in one step, or dispatched
 * into transit and received at the other end, short if the lorry was.
 */
export default function TransferForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/stock-transfers/"
      back="/stores/transfers"
      backLabel="Transfers"
      newTitle="New transfer"
      heading={(row) => String(row.number || "Transfer")}
      state={(row) => ({ label: String(row.status).replace(/_/g, " "),
        tone: row.status === "received" || row.status === "posted" ? "done" : row.status === "cancelled" ? "draft" : "open" })}
      permissions={{ add: "inventory.add_stocktransfer", change: "inventory.change_stocktransfer", delete: "inventory.delete_stocktransfer" }}
      editable={draft}
      fields={[
        { key: "transfer_date", label: "Date", kind: "date" },
        { key: "from_warehouse", label: "From", kind: "ref", ref: WAREHOUSE },
        { key: "to_warehouse", label: "To", kind: "ref", ref: WAREHOUSE },
        { key: "transit_warehouse", label: "Through", kind: "ref", ref: WAREHOUSE, hint: "A transit warehouse, for goods on a lorry; empty to move them at once" },
        { key: "reference", label: "Reference", kind: "text", hint: "Lorry, challan" },
        { key: "memo", label: "Memo", kind: "text", wide: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "inventory.change_stocktransfer", primary: true,
          when: (row) => draft(row) && !row.transit_warehouse, done: "Moved" },
        { label: "Dispatch", path: "dispatch", permission: "inventory.change_stocktransfer", primary: true,
          when: (row) => draft(row) && Boolean(row.transit_warehouse), done: "On its way" },
        { label: "Receive all", path: "receive", permission: "inventory.change_stocktransfer", primary: true,
          when: (row) => row.status === "in_transit", done: "Received" },
        { label: "Cancel", path: "cancel", permission: "inventory.change_stocktransfer", danger: true,
          when: (row) => draft(row) || row.status === "in_transit", done: "Cancelled",
          fields: [{ key: "memo", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Lines", permission: "inventory.view_stocktransfer", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "lot_code", label: "Batch", width: "9rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "outstanding", label: "Still to arrive", kind: "quantity", width: "10rem" },
        ],
        adder: { label: "Add a line", permission: "inventory.add_stocktransferline", when: draft,
          url: () => "/api/inventory/stock-transfer-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "lot", label: "Batch", kind: "pick", pick: LOT },
          ],
          body: (values, record) => ({ transfer: record.id, item: values.item, quantity: values.quantity, lot: values.lot }) },
        remover: { permission: "inventory.delete_stocktransferline", when: draft,
          url: (row) => `/api/inventory/stock-transfer-lines/${row.id}/` },
      }]}
    />
  );
}
