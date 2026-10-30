import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => row.status === "draft";
const out = (row: Row) => row.status === "out" && row.kind === "returnable";

// The gate's own pickers: an item or an asset by code and name, never its price.
const ITEM = { endpoint: "/api/inventory/gate-passes/items/", permission: "inventory.view_gatepass",
  label: (row: Record<string, unknown>) => `${String(row.sku)} · ${String(row.name)}` };
const ASSET = { endpoint: "/api/inventory/gate-passes/assets/", permission: "inventory.view_gatepass",
  label: (row: Record<string, unknown>) => `${String(row.number)} · ${String(row.name)}` };
const VISIT = { endpoint: "/api/inventory/gate-visits/", permission: "inventory.view_gatevisit", query: { status: "in" },
  label: (row: Record<string, unknown>) => `${String(row.number)} · ${String(row.vehicle_number)}` };

/** A gate pass: written, approved before its vehicle leaves, out with it, and back in part or in full. */
export default function GatePassForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/gate-passes/"
      back="/stores/gate-passes"
      backLabel="Gate passes"
      newTitle="New gate pass"
      heading={(row) => String(row.number || "Gate pass")}
      state={(row) => ({ label: `${String(row.status)}${row.overdue ? ", overdue" : ""}`,
        tone: row.status === "returned" ? "done" : row.status === "cancelled" ? "draft" : "open" })}
      permissions={{ add: "inventory.add_gatepass", change: "inventory.change_gatepass" }}
      editable={draft}
      fields={[
        { key: "kind", label: "Kind", kind: "choice", choices: [["returnable", "Returnable"], ["non_returnable", "Non-returnable"]], createOnly: true },
        { key: "pass_date", label: "Date", kind: "date" },
        { key: "party_name", label: "To", kind: "text", hint: "The repairer, the buyer" },
        { key: "purpose", label: "Purpose", kind: "text" },
        { key: "expected_return_date", label: "Due back", kind: "date", hint: "A returnable pass" },
        { key: "visit", label: "Vehicle", kind: "ref", ref: VISIT },
        { key: "approved_by_name", label: "Approved by", kind: "text", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Approve", path: "approve", permission: "inventory.approve_gatepass", primary: true, when: draft, done: "Approved" },
        { label: "Back in", path: "returned", permission: "inventory.add_gatepassreturn", primary: true, when: out, done: "Recorded",
          fields: (record) => [
            { key: "line", label: "What", kind: "choice",
              choices: ((record.lines as Row[]) ?? []).filter((line) => Number(line.outstanding) > 0)
                .map((line) => [String(line.id), String(line.label)] as [string, string]) },
            { key: "quantity", label: "How many", kind: "decimal" },
            { key: "returned_on", label: "On", kind: "date" },
            { key: "condition", label: "Condition", kind: "choice", choices: [["good", "Good"], ["damaged", "Damaged"]] },
            { key: "note", label: "Note", kind: "text" },
          ] },
        { label: "Cancel", path: "cancel", permission: "inventory.change_gatepass", danger: true,
          when: (row) => row.status === "draft" || row.status === "approved", done: "Cancelled",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Lines", permission: "inventory.view_gatepass", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "label", label: "What" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "returned", label: "Back", kind: "quantity", width: "8rem" },
          { key: "outstanding", label: "Still out", kind: "quantity", width: "8rem" },
        ],
        adder: { label: "Add a line", permission: "inventory.add_gatepassline", when: draft,
          url: () => "/api/inventory/gate-pass-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "asset", label: "Or an asset", kind: "pick", pick: ASSET },
            { key: "quantity", label: "Quantity", kind: "decimal" },
          ],
          body: (values, record) => ({ gate_pass: record.id, item: values.item, asset: values.asset,
            quantity: values.quantity || (values.asset ? "1" : values.quantity) }) },
        remover: { permission: "inventory.delete_gatepassline", when: draft,
          url: (row) => `/api/inventory/gate-pass-lines/${row.id}/` },
      }]}
    />
  );
}
