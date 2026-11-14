import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));
const TONE: Record<string, string> = { draft: "draft", applied: "done", rejected: "info" };

/**
 * One change to a recipe: the version it replaces, the new version made
 * with it (edited on its own page until this is applied), and the day
 * the new one takes over. Applying closes the old window the day before
 * and moves draft runs due from that day; released runs keep what they
 * froze. A change is decided once.
 */
export default function ChangeOrderForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/bom-change-orders/"
      back="/making/change-orders"
      backLabel="BOM change orders"
      newTitle="Change order"
      heading={(row) => `${String(row.number || "Change order")} · ${String(row.item_label ?? "")}`}
      state={(row) => ({ label: String(row.status).replace(/^./, (c) => c.toUpperCase()), tone: TONE[String(row.status)] ?? "draft" })}
      permissions={{ change: "manufacturing.change_bomchangeorder" }}
      editable={is("draft")}
      fields={[
        { key: "supersedes_label", label: "Replaces", readOnly: true },
        { key: "draft_label", label: "New version", readOnly: true },
        { key: "effective_from", label: "From", kind: "date", readOnly: true },
        { key: "reason", label: "Why", kind: "textarea", wide: true },
        { key: "created_by_name", label: "Raised by", readOnly: true },
        { key: "decision_note", label: "Decided", readOnly: true, existingOnly: true },
        { key: "decided_by_name", label: "Decided by", readOnly: true, existingOnly: true },
      ]}
      links={[
        { label: "The version it replaces", href: (row) => `/making/boms/${String(row.supersedes)}`, same: true },
        { label: "The new version", href: (row) => `/making/boms/${String(row.draft)}`, same: true },
      ]}
      actions={[
        { label: "Apply", path: "apply", permission: "manufacturing.apply_bomchangeorder", when: is("draft"), primary: true,
          done: "Applied: the new version takes over from its first day", fields: [{ key: "note", label: "Note", kind: "text" }] },
        { label: "Reject", path: "reject", permission: "manufacturing.apply_bomchangeorder", when: is("draft"), danger: true,
          done: "Rejected", fields: [{ key: "note", label: "Why", kind: "text", hint: "Say why, or the same change comes back" }] },
      ]}
    />
  );
}
