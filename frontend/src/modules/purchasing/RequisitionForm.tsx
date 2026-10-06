import { RecordScreen } from "../../views/RecordScreen";
import { ACCOUNT, ITEM, REQUESTER, UOM, VENDOR, WAREHOUSE, status } from "./refs";

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));

/**
 * Someone asking for something, before there is an order: asked for,
 * approved by whoever holds the budget, then ordered from a vendor chosen
 * then. What is approved is what is ordered, so its lines are fixed once
 * it is submitted.
 */
export default function RequisitionForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/requisitions/"
      back="/purchasing/requisitions"
      backLabel="Requisitions"
      newTitle="New requisition"
      heading={(row) => String(row.number || "Requisition")}
      state={status}
      permissions={{ add: "purchasing.add_purchaserequisition", change: "purchasing.change_purchaserequisition", delete: "purchasing.delete_purchaserequisition" }}
      editable={is("draft")}
      fields={[
        { key: "requested_by", label: "Asked by", kind: "pick", pick: REQUESTER },
        { key: "request_date", label: "Asked on", kind: "date" },
        { key: "needed_by", label: "Needed by", kind: "date" },
        { key: "justification", label: "Why", kind: "textarea", wide: true },
        { key: "decision_note", label: "Decided", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Submit", path: "submit", permission: "purchasing.change_purchaserequisition", when: is("draft"), primary: true, done: "Submitted" },
        { label: "Approve", path: "approve", permission: "purchasing.decide_purchaserequisition", when: is("submitted"), primary: true,
          done: "Approved", fields: [{ key: "note", label: "Note", kind: "text" }] },
        { label: "Reject", path: "reject", permission: "purchasing.decide_purchaserequisition", when: is("submitted"), danger: true,
          done: "Rejected", fields: [{ key: "note", label: "Why", kind: "text", hint: "Say why, or it comes back unchanged" }] },
        { label: "Order it", path: "order", permission: "purchasing.add_purchaseorder", when: is("approved"), primary: true,
          done: "Ordered", fields: [
            { key: "vendor", label: "From", kind: "pick", pick: VENDOR },
            { key: "order_date", label: "Ordered on", kind: "date" },
          ], then: (made) => `/purchasing/orders/${String(made.order)}` },
        { label: "Cancel", path: "cancel", permission: "purchasing.change_purchaserequisition", when: is("draft", "submitted", "approved", "rejected"),
          danger: true, done: "Cancelled" },
      ]}
      panels={[{
        title: "What is asked for", permission: "purchasing.view_purchaserequisitionline", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
          { key: "estimated_price", label: "About", kind: "money", width: "8rem" },
          { key: "estimated_value", label: "Comes to", kind: "money", width: "9rem" },
          { key: "quantity_ordered", label: "Ordered", kind: "quantity", width: "8rem" },
        ],
        adder: { label: "Add a line", permission: "purchasing.add_purchaserequisitionline", when: is("draft"),
          url: () => "/api/purchasing/requisition-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "uom", label: "Unit", kind: "ref", ref: UOM },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "estimated_price", label: "About, each", kind: "money", hint: "An estimate, not an agreed price" },
            { key: "expense_account", label: "Budget account", kind: "pick", pick: ACCOUNT },
            { key: "warehouse", label: "Wanted at", kind: "ref", ref: WAREHOUSE },
            { key: "suggested_vendor", label: "Vendor in mind", kind: "pick", pick: VENDOR },
          ],
          body: (values, record) => ({ ...values, requisition: record.id }) },
        remover: { permission: "purchasing.delete_purchaserequisitionline", when: is("draft"),
          url: (row) => `/api/purchasing/requisition-lines/${row.id}/` },
      }, {
        title: "On order", permission: "purchasing.view_purchaseorderline", endpoint: "/api/purchasing/purchase-order-lines/",
        query: (record) => ({ requisition_line__requisition: String(record.id) }),
        href: (row) => `/purchasing/orders/${String(row.order)}`,
        columns: [
          { key: "label", label: "Line" },
          { key: "quantity", label: "Ordered", kind: "quantity", width: "8rem" },
          { key: "unit_price", label: "Price", kind: "money", width: "8rem" },
          { key: "quantity_received", label: "Received", kind: "quantity", width: "8rem" },
        ],
      }]}
    />
  );
}
