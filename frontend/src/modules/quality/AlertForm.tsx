import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { WORK_CENTRE } from "../making/refs";
import { correctiveActions } from "./capa";
import { EMPLOYEE, ITEM, LOT } from "./refs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "open";

const MACHINE: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/machines/", permission: "manufacturing.view_machine",
  label: (row: Row) => `${String(row.code)} · ${String(row.name ?? "")}`,
};
const RUN: FieldDef["pick"] = {
  endpoint: "/api/manufacturing/work-orders/", permission: "manufacturing.view_workorder",
  label: (row: Row) => `${String(row.number || "Draft")} · ${String(row.item_sku ?? "")}`,
};
const SEVERITIES: [string, string][] = [["low", "Low"], ["medium", "Medium"], ["high", "High"]];

/**
 * One thing found wrong on the floor: where, what, how bad and who is to
 * deal with it. Actions hang off it as off a complaint; it closes with
 * its root cause once they are done, or is cancelled as not a defect.
 */
export default function AlertForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/quality-alerts/"
      back="/quality/alerts"
      backLabel="Quality alerts"
      newTitle="New quality alert"
      heading={(row) => `${String(row.number || "Quality alert")} · ${String(row.title ?? "")}`}
      state={(row) => ({ label: String(row.status === "cancelled" ? "Not a defect" : row.status),
        tone: row.status === "open" ? "open" : row.status === "closed" ? "done" : "draft" })}
      permissions={{ add: "manufacturing.add_qualityalert", change: "manufacturing.change_qualityalert" }}
      editable={open}
      fields={[
        { key: "title", label: "What was found", wide: true },
        { key: "raised_on", label: "Raised on", kind: "date", hint: "Empty: today" },
        { key: "raised_by", label: "Seen by", kind: "pick", pick: EMPLOYEE },
        { key: "severity", label: "How bad", kind: "choice", choices: SEVERITIES, initial: "medium" },
        { key: "owner", label: "To deal with it", kind: "pick", pick: EMPLOYEE },
        { key: "work_centre", label: "Work centre", kind: "ref", ref: WORK_CENTRE },
        { key: "machine", label: "Machine", kind: "pick", pick: MACHINE },
        { key: "item", label: "Item", kind: "pick", pick: ITEM },
        { key: "lot", label: "Batch", kind: "pick", pick: LOT },
        { key: "work_order", label: "Run", kind: "pick", pick: RUN },
        { key: "quantity_affected", label: "How many", kind: "decimal" },
        { key: "description", label: "Details", kind: "textarea", wide: true },
        { key: "root_cause", label: "Root cause", readOnly: true, existingOnly: true },
        { key: "cancelled_reason", label: "Not a defect because", readOnly: true, existingOnly: true },
        { key: "reopened_reason", label: "Reopened because", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Close", path: "close", permission: "manufacturing.change_qualityalert", when: open, primary: true, done: "Closed",
          fields: [{ key: "root_cause", label: "Root cause", kind: "text" }, { key: "by", label: "Decided by", kind: "pick", pick: EMPLOYEE }] },
        { label: "Not a defect", path: "cancel", permission: "manufacturing.change_qualityalert", when: open, danger: true, done: "Recorded as not a defect",
          fields: [{ key: "reason", label: "Why", kind: "text" }, { key: "by", label: "Decided by", kind: "pick", pick: EMPLOYEE }] },
        { label: "Reopen", path: "reopen", permission: "manufacturing.change_qualityalert", when: (row) => !open(row), done: "Reopened",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[correctiveActions("alert")]}
    />
  );
}
