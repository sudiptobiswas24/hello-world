import { dateTime } from "../../lib/format";
import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const inside = (row: Row) => row.status === "in";

const PURPOSES: [string, string][] = [
  ["inward_order", "In, against a purchase order"], ["inward_job_work", "In, against a job-work challan"],
  ["outward_delivery", "Out, against a delivery"], ["gate_pass", "Against a gate pass"], ["visitor", "Visitor"],
];

/** One vehicle at the gate: in, weighed, its passes, and out through the out-entry. */
export default function GateVisitForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/gate-visits/"
      back="/stores/gate"
      backLabel="Gate register"
      newTitle="Vehicle in"
      heading={(row) => `${String(row.number || "Visit")} ${String(row.vehicle_number ?? "")}`}
      state={(row) => ({ label: String(row.status) === "out" ? "left" : String(row.status),
        tone: row.status === "out" ? "done" : row.status === "cancelled" ? "draft" : "open" })}
      permissions={{ add: "inventory.add_gatevisit", change: "inventory.change_gatevisit" }}
      editable={inside}
      fields={[
        { key: "vehicle_number", label: "Vehicle", kind: "text", hint: "MH 12 AB 1234" },
        { key: "driver_name", label: "Driver", kind: "text" },
        { key: "driver_mobile", label: "Driver's mobile", kind: "text" },
        { key: "transporter_name", label: "Transporter", kind: "text" },
        { key: "purpose", label: "For", kind: "choice", choices: PURPOSES },
        { key: "reference", label: "Papers", kind: "text", hint: "The order, challan or delivery number they carry" },
        { key: "carried", label: "Carried", kind: "textarea", wide: true },
        { key: "in_at", label: "In", kind: "text", readOnly: true, existingOnly: true, show: (row) => dateTime(String(row.in_at)) },
        { key: "out_at", label: "Out", kind: "text", readOnly: true, existingOnly: true,
          show: (row) => (row.out_at ? dateTime(String(row.out_at)) : "Still in") },
      ]}
      actions={[
        { label: "Weigh", path: "weigh", permission: "inventory.add_weighing",
          when: (row) => inside(row) && row.purpose !== "visitor", done: "Weighing begun",
          fields: [{ key: "scale_code", label: "Weighbridge scale", kind: "text", initial: "WB-1" }],
          then: (result) => `/stores/weighings/${String(result.id)}` },
        { label: "Out", path: "leave", permission: "inventory.change_gatevisit", primary: true, when: inside, done: "Left" },
        { label: "Cancel", path: "cancel", permission: "inventory.change_gatevisit", danger: true,
          when: (row) => row.status !== "cancelled", done: "Cancelled", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[
        { title: "Weighings", permission: "inventory.view_weighing", endpoint: "", query: () => ({}),
          rows: (record) => (record.weighings as Row[]) ?? [],
          href: (row) => `/stores/weighings/${row.id}`,
          columns: [
            { key: "number", label: "Slip", width: "10rem" },
            { key: "gross_kg", label: "Gross kg", kind: "quantity", width: "9rem" },
            { key: "tare_kg", label: "Tare kg", kind: "quantity", width: "9rem" },
            { key: "net_kg", label: "Net kg", kind: "quantity", width: "9rem" },
            { key: "typed", label: "Typed", width: "6rem", render: (row) => ((row.typed as { any?: boolean })?.any ? "Typed" : "") },
            { key: "state", label: "State", width: "7rem" },
          ] },
        { title: "Gate passes", permission: "inventory.view_gatepass", endpoint: "", query: () => ({}),
          rows: (record) => (record.passes as Row[]) ?? [],
          href: (row) => `/stores/gate-passes/${row.id}`,
          columns: [{ key: "number", label: "Pass" }, { key: "status", label: "State", width: "8rem" }] },
      ]}
    />
  );
}
