import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const live = (row: Row) => row.state === "weighing";
const missing = (side: string) => (row: Row) => live(row) && (row[`${side}_kg`] === null || row[`${side}_kg`] === undefined);
const typed = (side: string) => (row: Row) => ((row.typed as Record<string, boolean>)?.[side] ? " (typed)" : "");

const REASONS: [string, string][] = [
  ["scale_offline", "Scale bridge not connected"], ["calibration", "Weighbridge under calibration"],
  ["power", "Power failure"], ["other", "Other"],
];

/** A weighbridge slip: gross and tare from the scale, or typed by who may with a reason. */
export default function WeighingForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/weighings/"
      back="/stores/gate"
      backLabel="Gate register"
      newTitle="Weighing"
      heading={(row) => `${String(row.number)} ${String(row.vehicle_number ?? "")}`}
      state={(row) => ({ label: String(row.state), tone: row.state === "weighed" ? "done" : row.state === "weighing" ? "open" : "draft" })}
      permissions={{}}
      editable={() => false}
      fields={[
        { key: "visit_number", label: "Visit", kind: "text", readOnly: true },
        { key: "scale_code", label: "Scale", kind: "text", readOnly: true },
        { key: "gross_kg", label: "Gross kg", kind: "decimal", readOnly: true, show: (row) => `${String(row.gross_kg ?? "—")}${typed("gross")(row)}` },
        { key: "tare_kg", label: "Tare kg", kind: "decimal", readOnly: true, show: (row) => `${String(row.tare_kg ?? "—")}${typed("tare")(row)}` },
        { key: "net_kg", label: "Net kg", kind: "decimal", readOnly: true,
          show: (row) => `${String(row.net_kg ?? "—")}${(row.typed as { any?: boolean })?.any ? " (typed)" : ""}` },
        { key: "replace_reason", label: "Replaced because", kind: "text", readOnly: true },
      ]}
      actions={[
        { label: "Gross from the scale", path: "record", permission: "inventory.change_weighing", primary: true,
          when: missing("gross"), done: "Gross weighed", body: () => ({ side: "gross" }),
          url: (record) => `/api/manufacturing/weighbridge/${String(record.id)}/from-scale/` },
        { label: "Tare from the scale", path: "record", permission: "inventory.change_weighing", primary: true,
          when: missing("tare"), done: "Tare weighed", body: () => ({ side: "tare" }),
          url: (record) => `/api/manufacturing/weighbridge/${String(record.id)}/from-scale/` },
        { label: "Type a weight", path: "record", permission: "inventory.type_weight",
          when: (row) => missing("gross")(row) || missing("tare")(row), done: "Typed",
          fields: [
            { key: "side", label: "Which", kind: "choice", choices: [["gross", "Gross"], ["tare", "Tare"]] },
            { key: "kg", label: "Kilogrammes", kind: "decimal", places: 3 },
            { key: "reason", label: "Why not the scale", kind: "choice", choices: REASONS },
            { key: "note", label: "Note", kind: "text" },
          ],
          body: (values) => ({ ...values, source: "typed" }) },
        { label: "Replace", path: "replace", permission: "inventory.change_weighing", danger: true,
          when: (row) => row.state === "weighing" || row.state === "weighed", done: "Replaced",
          fields: [{ key: "reason", label: "Why", kind: "text" }],
          then: (result) => `/stores/weighings/${String(result.id)}` },
      ]}
    />
  );
}
