import { RecordScreen } from "../../views/RecordScreen";
import { CUSTOMER, EMPLOYEE } from "./refs";

type Row = Record<string, unknown> & { id: number };
const open = (row: Row) => row.status === "open";

const CATEGORIES: [string, string][] = [
  ["weight", "Weight or size"], ["strength", "Strength or bursting"], ["seam", "Stitching or seam"],
  ["print", "Print"], ["lamination", "Lamination or coating"], ["contamination", "Contamination"],
  ["count", "Count short"], ["other", "Other"],
];

/**
 * A complaint: the batches it names, traced back to what they were made
 * from and forward to who else holds them, and the actions taken. It is
 * closed with its root cause, or rejected with the reason.
 */
export default function ComplaintForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/complaints/"
      back="/quality/complaints"
      backLabel="Complaints"
      newTitle="New complaint"
      heading={(row) => `${String(row.number)} · ${String(row.customer_name ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: row.status === "open" ? "open" : row.status === "closed" ? "done" : "draft" })}
      permissions={{ add: "manufacturing.add_complaint", change: "manufacturing.change_complaint" }}
      editable={open}
      fields={[
        { key: "customer", label: "Customer", kind: "pick", pick: CUSTOMER, createOnly: true, show: (row) => String(row.customer_name ?? "") },
        { key: "received_on", label: "Received", kind: "date" },
        { key: "category", label: "About", kind: "choice", choices: CATEGORIES },
        { key: "quantity_affected", label: "How many", kind: "decimal" },
        { key: "description", label: "What they said", kind: "textarea" },
        { key: "root_cause", label: "Root cause", readOnly: true },
        { key: "rejection_reason", label: "Rejected because", readOnly: true },
      ]}
      actions={[
        { label: "Name a batch", path: "lots", permission: "manufacturing.change_complaint", when: open, done: "Batch added",
          fields: [{ key: "lot", label: "Batch code", kind: "text" }, { key: "quantity", label: "How many of it", kind: "decimal" }] },
        { label: "Close", path: "close", permission: "manufacturing.change_complaint", when: open, primary: true, done: "Closed",
          fields: [{ key: "root_cause", label: "Root cause", kind: "text" }, { key: "by", label: "Decided by", kind: "pick", pick: EMPLOYEE }] },
        { label: "Reject", path: "reject", permission: "manufacturing.change_complaint", when: open, danger: true, done: "Rejected",
          fields: [{ key: "reason", label: "Why", kind: "text" }, { key: "by", label: "Decided by", kind: "pick", pick: EMPLOYEE }] },
        { label: "Reopen", path: "reopen", permission: "manufacturing.change_complaint", when: (row) => !open(row), done: "Reopened",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[
        { title: "Batches", permission: "manufacturing.view_complaint", endpoint: "", query: () => ({}),
          rows: (record) => ((record.lots as Row[]) ?? []).map((row, index) => ({ ...row, id: index })),
          columns: [{ key: "lot", label: "Batch" }, { key: "item", label: "Item" }, { key: "quantity", label: "How many", kind: "quantity" }] },
        { title: "Actions", permission: "manufacturing.view_correctiveaction", endpoint: "", query: () => ({}),
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
            body: (values, record) => ({ complaint: record.id, kind: values.kind, description: values.description,
              owner: values.owner, due_on: values.due_on }) } },
      ]}
    />
  );
}
