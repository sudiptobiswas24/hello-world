import { RecordScreen } from "../../views/RecordScreen";
import { CUSTOMER, draft, postedState } from "./partyRefs";
import { WAREHOUSE } from "./refs";

type Row = Record<string, unknown> & { id: number };

/** The customer's own material going back unused, against the receipt line it came in on. */
export default function CustomerReturnForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/customer-material-returns/"
      back="/stores/customer-material-back"
      backLabel="Customer material back"
      newTitle="Return customer material"
      heading={(row) => `${String(row.number || "Return")} · ${String(row.customer_name ?? "")}`}
      state={postedState}
      permissions={{ add: "manufacturing.add_customermaterialreturn", change: "manufacturing.change_customermaterialreturn",
        delete: "manufacturing.delete_customermaterialreturn" }}
      editable={draft}
      fields={[
        { key: "customer", label: "Customer", kind: "pick", pick: CUSTOMER, createOnly: true, show: (row) => String(row.customer_name) },
        { key: "warehouse", label: "From", kind: "ref", ref: WAREHOUSE },
        { key: "returned_on", label: "Returned", kind: "date" },
        { key: "voided_reason", label: "Voided because", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "manufacturing.change_customermaterialreturn", when: draft, primary: true,
          done: "Posted" },
        { label: "Void", path: "void", permission: "manufacturing.change_customermaterialreturn", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Material", permission: "manufacturing.view_customermaterialreturnline", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "lot_code", label: "Batch", width: "9rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
        ],
        adder: { label: "Add material", permission: "manufacturing.add_customermaterialreturnline", when: draft,
          url: () => "/api/manufacturing/customer-material-return-lines/",
          fields: (record) => [
            { key: "receipt_line", label: "Received on", kind: "pick", pick: {
              endpoint: "/api/manufacturing/customer-material-receipt-lines/",
              permission: "manufacturing.view_customermaterialreceiptline",
              query: { receipt__customer: String(record.customer), receipt__posted: "true" },
              label: (row) => `${String(row.item_label)} · ${String(row.lot_code || "no batch")} · ${String(row.quantity)}` } },
            { key: "quantity", label: "Quantity", kind: "decimal" },
          ],
          body: (values, record) => ({ material_return: record.id, ...values }) },
        remover: { permission: "manufacturing.delete_customermaterialreturnline", when: draft,
          url: (row) => `/api/manufacturing/customer-material-return-lines/${row.id}/` },
      }]}
    />
  );
}
