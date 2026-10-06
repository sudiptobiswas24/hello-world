import { RecordScreen } from "../../views/RecordScreen";
import { DISPOSITIONS, INSTRUMENT, LOT, PERSON, PLAN } from "./refs";

type Row = Record<string, unknown> & { id: number };
const draft = (row: Row) => !row.posted;

/**
 * One batch against its plan: the readings, the result the limits give,
 * and the decision. Posted, it is what releases or holds the batch; a
 * wrong one is voided, never edited.
 */
export default function InspectionForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/inspections/"
      back="/quality/inspections"
      backLabel="Inspections"
      newTitle="New inspection"
      heading={(row) => `${String(row.number || "Inspection")} · ${String(row.lot_code ?? "")}`}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" }
        : row.posted ? { label: String(row.lot_status ?? "posted"), tone: row.result === "fail" ? "warn" : "done" }
        : { label: "Draft", tone: "open" })}
      permissions={{ add: "quality.add_inspection", change: "quality.change_inspection" }}
      editable={draft}
      fields={[
        { key: "lot", label: "Batch", kind: "pick", pick: LOT, createOnly: true, show: (row) => String(row.lot_code) },
        { key: "plan", label: "Plan", kind: "ref", ref: PLAN, createOnly: true },
        { key: "inspected_on", label: "Inspected on", kind: "date" },
        { key: "inspected_by", label: "By", kind: "pick", pick: PERSON, show: (row) => String(row.inspected_by_name || "—") },
        { key: "lot_size", label: "Batch size", kind: "integer", hint: "Sets the sample under an AQL plan" },
        { key: "disposition", label: "Decision", kind: "choice", choices: DISPOSITIONS },
        { key: "decision_note", label: "Why", kind: "text", wide: true },
        { key: "notes", label: "Notes", kind: "textarea" },
        { key: "result", label: "Result", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "quality.change_inspection", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "quality.change_inspection", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Readings", permission: "quality.view_inspection", endpoint: "", query: () => ({}),
        rows: (record) => (record.readings as Row[]) ?? [],
        columns: [
          { key: "characteristic", label: "What" },
          { key: "sample_reference", label: "Sample", width: "8rem" },
          { key: "value", label: "Reading", kind: "quantity", width: "8rem" },
          { key: "limits", label: "Limits", width: "10rem", render: (row) => [row.lower_limit, row.upper_limit].filter((v) => v !== null && v !== undefined).join(" to ") },
          { key: "passed", label: "", width: "6rem", render: (row) => (row.passed === null ? "" : row.passed ? "Pass" : "Fail") },
        ],
        adder: { label: "Add a reading", permission: "quality.change_inspection", when: draft,
          url: () => "/api/quality/readings/",
          fields: (record) => [
            { key: "plan_line", label: "What", kind: "ref", ref: { endpoint: "/api/quality/plan-lines/",
              permission: "quality.view_planline", query: { plan: String(record.plan) },
              label: (row) => String(row.characteristic_label ?? row.id) } },
            { key: "value", label: "Reading", kind: "decimal", hint: "For a measured characteristic" },
            { key: "present", label: "Present", kind: "bool", hint: "For a present-or-absent one" },
            { key: "sample_reference", label: "Sample", kind: "text" },
            { key: "instrument", label: "Instrument", kind: "ref", ref: INSTRUMENT },
          ],
          body: (values, record) => ({ inspection: record.id, plan_line: values.plan_line, value: values.value,
            present: values.present, sample_reference: values.sample_reference, instrument: values.instrument }) },
        remover: { permission: "quality.change_inspection", when: draft, url: (row) => `/api/quality/readings/${row.id}/` },
      }]}
    />
  );
}
