import { dateTime } from "../../lib/format";
import { RecordScreen } from "../../views/RecordScreen";

/**
 * One failure, as the person saw it (the reference) and as whoever keeps
 * the system needs it (the traceback). Dealt with, it carries a note of
 * what was done; reopened if it was not.
 */
export default function ProblemForm() {
  return (
    <RecordScreen
      endpoint="/api/core/errors/"
      back="/settings/problems"
      backLabel="Problems"
      newTitle="Problem"
      heading={(row) => `${String(row.ref ?? "")} · ${String(row.kind ?? "")}`}
      state={(row) => (row.resolved_at ? { label: "dealt with", tone: "done" } : { label: "open", tone: "cancelled" })}
      permissions={{}}
      editable={() => false}
      fields={[
        { key: "happened_at", label: "When", readOnly: true, show: (row) => dateTime(String(row.happened_at ?? "")) },
        { key: "user_name", label: "Who hit it", readOnly: true },
        { key: "method", label: "Request", readOnly: true, show: (row) => `${String(row.method ?? "")} ${String(row.path ?? "")}` },
        { key: "message", label: "Message", readOnly: true, wide: true },
        { key: "resolved_by_name", label: "Dealt with by", readOnly: true, existingOnly: true },
        { key: "note", label: "What was done", readOnly: true, wide: true },
        { key: "traceback", label: "Traceback", readOnly: true, wide: true,
          show: (row) => <pre className="sentences">{String(row.traceback ?? "")}</pre> },
      ]}
      actions={[
        { label: "Dealt with", path: "resolve", permission: "core.change_servererror", primary: true, done: "Marked dealt with",
          when: (row) => !row.resolved_at,
          fields: [{ key: "note", label: "What was done", wide: true }] },
        { label: "Reopen", path: "reopen", permission: "core.change_servererror", done: "Reopened",
          when: (row) => Boolean(row.resolved_at) },
      ]}
    />
  );
}
