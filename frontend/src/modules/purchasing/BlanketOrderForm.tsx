import { RecordScreen } from "../../views/RecordScreen";
import { CURRENCY, ITEM, UOM, VENDOR, status } from "./refs";

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));
const lines = (record: Row) => (record.lines as Row[]) ?? [];

/**
 * A price and volume agreed with a vendor for a period, called off by
 * releases rather than delivered at once. The price and volume are fixed
 * once it is confirmed: each release is priced from them.
 */
export default function BlanketOrderForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/blanket-orders/"
      back="/purchasing/blanket-orders"
      backLabel="Blanket orders"
      newTitle="New blanket order"
      heading={(row) => String(row.number || "Blanket order")}
      state={status}
      permissions={{ add: "purchasing.add_blanketorder", change: "purchasing.change_blanketorder", delete: "purchasing.delete_blanketorder" }}
      editable={is("draft")}
      fields={[
        { key: "vendor", label: "Vendor", kind: "pick", pick: VENDOR },
        { key: "reference", label: "Their reference", kind: "text" },
        { key: "start_date", label: "From", kind: "date" },
        { key: "end_date", label: "To", kind: "date" },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY, hint: "The vendor's, unless another is agreed" },
      ]}
      actions={[
        { label: "Confirm", path: "confirm", permission: "purchasing.change_blanketorder", when: is("draft"), primary: true, done: "Confirmed" },
        { label: "Close early", path: "close", permission: "purchasing.change_blanketorder", when: is("draft", "confirmed"), danger: true,
          done: "Closed: releases already made stand" },
      ]}
      panels={[{
        title: "Agreed", permission: "purchasing.view_blanketorderline", endpoint: "", query: () => ({}),
        rows: (record) => lines(record),
        columns: [
          { key: "item_label", label: "Item" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
          { key: "unit_price", label: "Each", kind: "money", width: "8rem" },
          { key: "net_amount", label: "Comes to", kind: "money", width: "9rem" },
          { key: "quantity_released", label: "Called off", kind: "quantity", width: "8rem" },
          { key: "quantity_remaining", label: "Left", kind: "quantity", width: "8rem" },
        ],
        adder: { label: "Add a line", permission: "purchasing.add_blanketorderline", when: is("draft"),
          url: () => "/api/purchasing/blanket-order-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "uom", label: "Unit", kind: "ref", ref: UOM },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "unit_price", label: "Each", kind: "money" },
            { key: "discount_percent", label: "Discount %", kind: "decimal", initial: "0" },
          ],
          body: (values, record) => ({ ...values, blanket: record.id }) },
        remover: { permission: "purchasing.delete_blanketorderline", when: is("draft"),
          url: (row) => `/api/purchasing/blanket-order-lines/${row.id}/` },
      }, {
        title: "Called off", permission: "purchasing.view_purchaseorderline", endpoint: "/api/purchasing/purchase-order-lines/",
        query: (record) => ({ blanket_line__blanket: String(record.id) }),
        href: (row) => `/purchasing/orders/${String(row.order)}`,
        columns: [
          { key: "label", label: "Line" },
          { key: "quantity", label: "Ordered", kind: "quantity", width: "8rem" },
          { key: "unit_price", label: "Each", kind: "money", width: "8rem" },
          { key: "quantity_received", label: "Received", kind: "quantity", width: "8rem" },
        ],
        adder: { label: "Call some off", permission: "purchasing.add_purchaseorder", when: is("confirmed"),
          url: (record) => `/api/purchasing/blanket-orders/${record.id}/release/`,
          fields: (record) => [
            { key: "line", label: "Item", kind: "choice",
              choices: lines(record).map((line) => [String(line.id), `${String(line.item_label)} (${String(line.quantity_remaining)} left)`]) },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "order_date", label: "Ordered on", kind: "date" },
            { key: "expected_date", label: "Wanted by", kind: "date" },
          ],
          body: (values) => ({ quantities: { [String(values.line)]: values.quantity }, order_date: values.order_date || undefined,
            expected_date: values.expected_date || undefined }) },
      }]}
    />
  );
}
