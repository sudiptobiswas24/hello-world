import { RecordScreen } from "../../views/RecordScreen";
import { CHARACTERISTIC, ITEM } from "./refs";

type Row = Record<string, unknown> & { id: number };
const typed = (row: Row) => !row.is_computed;

/**
 * A plan: each characteristic with its target and limits, how many to
 * test and how the readings are judged. A computed plan is worked out
 * from the bag's specification and changed there, not here.
 */
export default function PlanForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/plans/"
      back="/quality/plans"
      backLabel="Inspection plans"
      newTitle="New inspection plan"
      heading={(row) => String(row.name)}
      state={(row) => (row.is_computed ? { label: "From the specification", tone: "info" } : row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "quality.add_inspectionplan", change: "quality.change_inspectionplan" }}
      editable={typed}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: ITEM, show: (row) => String(row.item_label ?? "—") },
        { key: "name", label: "Plan" },
        { key: "is_mandatory", label: "Holds the batch until passed", kind: "bool", initial: true },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "valid_to", label: "To", kind: "date" },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
      panels={[{
        title: "Checks", permission: "quality.view_inspectionplan", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "line_number", label: "#", width: "3rem" },
          { key: "characteristic_label", label: "What" },
          { key: "target", label: "Target", kind: "quantity", width: "8rem" },
          { key: "lower_limit", label: "Lowest", kind: "quantity", width: "8rem" },
          { key: "upper_limit", label: "Highest", kind: "quantity", width: "8rem" },
          { key: "sample_size", label: "Samples", kind: "quantity", width: "7rem" },
          { key: "evaluation", label: "Judged by", width: "9rem", render: (row) => (row.evaluation === "mean" ? "The average" : "Every reading") },
        ],
        adder: { label: "Add a check", permission: "quality.add_planline", when: typed,
          url: () => "/api/quality/plan-lines/",
          fields: [
            { key: "characteristic", label: "What", kind: "ref", ref: CHARACTERISTIC },
            { key: "target", label: "Target", kind: "decimal" },
            { key: "lower_limit", label: "Lowest", kind: "decimal" },
            { key: "upper_limit", label: "Highest", kind: "decimal" },
            { key: "sample_size", label: "Samples", kind: "integer", initial: "1" },
            { key: "evaluation", label: "Judged by", kind: "choice", choices: [["every", "Every reading must pass"], ["mean", "The average must pass"]] },
            { key: "aql", label: "AQL", kind: "decimal", hint: "Instead of a fixed sample, for counted defects" },
          ],
          body: (values, record) => ({ ...values, id: undefined, plan: record.id }) },
        remover: { permission: "quality.delete_planline", when: typed, url: (row) => `/api/quality/plan-lines/${row.id}/` },
      }]}
    />
  );
}
