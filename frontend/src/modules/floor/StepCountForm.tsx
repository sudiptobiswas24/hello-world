import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const STEP: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/work-order-operations/", permission: "manufacturing.view_workorderoperation",
  query: { work_order__status: "released" }, label: (row: Row) => String(row.label),
};
const MACHINE: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/machines/", permission: "manufacturing.view_machine", label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};

/**
 * What one step of a run passed on to the next, counted before it moved:
 * the step's good output, from which its scrap and the work waiting before
 * the next step follow. Recorded, never edited; a wrong count is voided.
 */
export default function StepCountForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/operation-reports/"
      createUrl="/api/manufacturing/operation-reports/record/"
      back="/production/step-counts"
      backLabel="Step counts"
      newTitle="New step count"
      heading={(row) => `${String(row.step ?? "")} · ${String(row.reported_on ?? "")}`}
      state={(row) => (row.voided_at ? { label: "Voided", tone: "draft" } : { label: "Counted", tone: "done" })}
      permissions={{ add: "manufacturing.add_operationreport" }}
      fields={[
        { key: "operation", label: "Step", kind: "pick", pick: STEP, createOnly: true, show: (row) => String(row.step ?? "") },
        { key: "quantity_good", label: "Passed on", kind: "decimal", createOnly: true },
        { key: "reported_on", label: "Day", kind: "date", createOnly: true },
        { key: "machine", label: "Machine", kind: "ref", ref: MACHINE, createOnly: true },
        { key: "memo", label: "Note", createOnly: true, wide: true },
        { key: "voided_reason", label: "Voided because", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Void", path: "void", permission: "manufacturing.add_operationreport", danger: true,
          when: (row) => !row.voided_at, done: "Voided", fields: [{ key: "reason", label: "Why", wide: true }] },
      ]}
    />
  );
}
