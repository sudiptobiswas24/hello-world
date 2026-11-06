import type { PanelDef } from "../../views/RecordScreen";
import { EMPLOYEE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "open";

/**
 * The corrective actions on a complaint or on a quality alert: the one
 * panel for both, so what is fixed for one is fixed for the other.
 */
export function correctiveActions(parent: "complaint" | "alert"): PanelDef {
  return {
    title: "Actions", permission: "manufacturing.view_correctiveaction", endpoint: "", query: () => ({}),
    rows: (record) => (record.actions as Row[]) ?? [],
    columns: [
      { key: "kind", label: "Kind", kind: "status", width: "9rem" },
      { key: "description", label: "What" },
      { key: "due_on", label: "Due", kind: "date", width: "8rem" },
      { key: "done_on", label: "Done", kind: "date", width: "8rem" },
      { key: "verified_on", label: "Verified", kind: "date", width: "8rem" },
    ],
    rowAction: { label: "Done", permission: "manufacturing.change_correctiveaction", when: (row) => !row.done_on,
      url: (row) => `/api/manufacturing/corrective-actions/${row.id}/done/`, done: "Marked done" },
    adder: { label: "Add an action", permission: "manufacturing.add_correctiveaction", when: open,
      url: () => "/api/manufacturing/corrective-actions/",
      fields: [
        { key: "kind", label: "Kind", kind: "choice", choices: [["containment", "Containment"], ["corrective", "Corrective"], ["preventive", "Preventive"]] },
        { key: "description", label: "What", kind: "text" },
        { key: "owner", label: "Who", kind: "pick", pick: EMPLOYEE },
        { key: "due_on", label: "By", kind: "date" },
      ],
      body: (values, record) => ({ [parent]: record.id, kind: values.kind, description: values.description,
        owner: values.owner, due_on: values.due_on }) },
  };
}
