import { RecordScreen } from "../../views/RecordScreen";
import { ORDER_LINE } from "./extraRefs";

/**
 * The customer asking for part of a confirmed line by a date. What is not
 * yet called off stays owed at the line's own delivery date.
 */
export default function CallOffForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/call-offs/"
      back="/sales/call-offs"
      backLabel="Call-offs"
      newTitle="New call-off"
      heading={(row) => `${String(row.line_label ?? "")} · ${String(row.due_on ?? "")}`}
      permissions={{ add: "sales.add_calloff", change: "sales.change_calloff", delete: "sales.delete_calloff" }}
      fields={[
        { key: "line", label: "Order line", kind: "pick", pick: ORDER_LINE({ order__status: "confirmed" }), createOnly: true,
          show: (row) => String(row.line_label ?? "") },
        { key: "due_on", label: "Due", kind: "date" },
        { key: "quantity", label: "Quantity", kind: "decimal" },
        { key: "reference", label: "Their reference", hint: "Their schedule or release number" },
      ]}
    />
  );
}
