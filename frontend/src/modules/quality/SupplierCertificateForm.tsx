import { RecordScreen } from "../../views/RecordScreen";
import { LOT } from "./refs";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => !row.posted;
const standing = (row: Row) => Boolean(row.posted) && !row.voided_at;

/**
 * A supplier's certificate of analysis for one batch bought in (L28). Posted,
 * each value is read against the plan in force the day the batch came in, and
 * the limits are kept on the line; a wrong one is voided, never edited.
 */
export default function SupplierCertificateForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/supplier-certificates/"
      back="/quality/supplier-certificates"
      backLabel="Supplier certificates"
      newTitle="New supplier certificate"
      heading={(row) => `${String(row.number || "Supplier certificate")} · ${String(row.lot_code ?? "")}`}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" }
        : !row.posted ? { label: "Draft", tone: "open" }
        : row.result === "within" ? { label: "Within specification", tone: "done" }
        : { label: "Outside specification", tone: "warn" })}
      note={(row) => (row && row.lot_held_because
        ? <div className="note warn" role="note" aria-label="Batch held">{String(row.lot_held_because)}</div>
        : null)}
      permissions={{ add: "quality.add_suppliercertificate", change: "quality.change_suppliercertificate" }}
      editable={draft}
      fields={[
        { key: "lot", label: "Batch", kind: "pick", pick: LOT, createOnly: true, show: (row) => String(row.lot_code) },
        { key: "vendor_name", label: "Supplier", readOnly: true, existingOnly: true },
        { key: "supplier_number", label: "Their certificate number" },
        { key: "issued_on", label: "Dated", kind: "date" },
        { key: "recorded_on", label: "Read in on", kind: "date" },
        { key: "plan_name", label: "Read against", readOnly: true, existingOnly: true },
        { key: "verdict", label: "Verdict", readOnly: true, existingOnly: true, wide: true },
        { key: "voided_reason", label: "Voided because", readOnly: true, existingOnly: true,
          show: (row) => (row.voided_at ? `${String(row.voided_reason)} (${String(row.voided_on)})` : "—") },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "quality.change_suppliercertificate", when: draft, primary: true,
          done: "Posted" },
        { label: "Void", path: "void", permission: "quality.change_suppliercertificate", danger: true,
          when: standing, done: "Voided",
          fields: [{ key: "reason", label: "Why", kind: "text" }, { key: "date", label: "On", kind: "date" }] },
      ]}
      panels={[{
        title: "Values", permission: "quality.view_suppliercertificate", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "characteristic_label", label: "What" },
          { key: "value_shown", label: "Certified", width: "8rem",
            render: (row) => (row.missing ? "Missing" : String(row.value_shown)) },
          { key: "limits_shown", label: "Limits when posted", width: "12rem" },
          { key: "within", label: "", width: "7rem",
            render: (row) => (row.within === null || row.within === undefined ? "" : row.within ? "Within" : "Outside") },
        ],
        adder: { label: "Add a value", permission: "quality.change_suppliercertificate", when: draft,
          url: () => "/api/quality/supplier-certificate-lines/",
          fields: [
            { key: "characteristic", label: "What", kind: "ref", ref: { endpoint: "/api/quality/characteristics/",
              permission: "quality.view_characteristic", query: { is_active: "true" },
              label: (row) => `${String(row.code)} · ${String(row.name)}` } },
            { key: "value", label: "Value", kind: "decimal", hint: "For a measured characteristic" },
            { key: "present", label: "Present", kind: "bool", hint: "For a present-or-absent one" },
          ],
          body: (values, record) => ({ certificate: record.id, characteristic: values.characteristic,
            value: values.value, present: values.present }) },
        remover: { permission: "quality.change_suppliercertificate", when: draft,
          url: (row) => `/api/quality/supplier-certificate-lines/${row.id}/` },
      }]}
    />
  );
}
