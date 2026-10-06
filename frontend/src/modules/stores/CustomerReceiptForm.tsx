import { RecordScreen } from "../../views/RecordScreen";
import { CUSTOMER, draft, postedState } from "./partyRefs";
import { ITEM, LOT, WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };

/** The customer's own material received to convert for them: held for them, never ours, never valued. */
export default function CustomerReceiptForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/customer-material-receipts/"
      back="/stores/customer-material"
      backLabel="Customer material in"
      newTitle="Receive customer material"
      heading={(row) => `${String(row.number || "Receipt")} · ${String(row.customer_name ?? "")}`}
      state={postedState}
      permissions={{ add: "manufacturing.add_customermaterialreceipt", change: "manufacturing.change_customermaterialreceipt",
        delete: "manufacturing.delete_customermaterialreceipt" }}
      editable={draft}
      fields={[
        { key: "customer", label: "Customer", kind: "pick", pick: CUSTOMER, createOnly: true, show: (row) => String(row.customer_name) },
        { key: "warehouse", label: "Into", kind: "ref", ref: WAREHOUSE },
        { key: "received_on", label: "Received", kind: "date" },
        { key: "their_challan", label: "Their challan" },
        { key: "their_challan_date", label: "Its date", kind: "date" },
        { key: "voided_reason", label: "Voided because", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "manufacturing.change_customermaterialreceipt", when: draft, primary: true,
          done: "Posted" },
        { label: "Void", path: "void", permission: "manufacturing.change_customermaterialreceipt", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Material", permission: "manufacturing.view_customermaterialreceiptline", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "lot_code", label: "Batch", width: "9rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "declared_value", label: "Declared value", kind: "money", width: "10rem" },
        ],
        adder: { label: "Add material", permission: "manufacturing.add_customermaterialreceiptline", when: draft,
          url: () => "/api/manufacturing/customer-material-receipt-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "lot", label: "Batch", kind: "pick", pick: LOT },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "declared_value", label: "Declared value", kind: "money", initial: "0" },
          ],
          body: (values, record) => ({ receipt: record.id, ...values }) },
        remover: { permission: "manufacturing.delete_customermaterialreceiptline", when: draft,
          url: (row) => `/api/manufacturing/customer-material-receipt-lines/${row.id}/` },
      }]}
    />
  );
}
