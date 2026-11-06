import { RecordScreen } from "../../views/RecordScreen";
import { EMPLOYEE } from "../quality/refs";

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));

/**
 * One appraisal: written by the reviewer as a draft nobody else reads,
 * submitted with a rating out of five, and acknowledged by the person
 * with a comment of their own that is kept with it.
 */
export default function AppraisalForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/appraisals/"
      back="/payroll/appraisals"
      backLabel="Appraisals"
      newTitle="New appraisal"
      heading={(row) => `${String(row.employee_name ?? "Appraisal")} · ${String(row.period_start ?? "")} to ${String(row.period_end ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: row.status === "draft" ? "draft" : row.status === "submitted" ? "open" : "done" })}
      permissions={{ add: "hr.add_appraisal", change: "hr.change_appraisal", delete: "hr.delete_appraisal" }}
      editable={is("draft")}
      fields={[
        { key: "employee", label: "Who", kind: "pick", pick: EMPLOYEE, createOnly: true, show: (row) => String(row.employee_name ?? "") },
        { key: "reviewer", label: "By", kind: "pick", pick: EMPLOYEE, createOnly: true, hint: "Empty: yourself",
          show: (row) => String(row.reviewer_name ?? "") },
        { key: "period_start", label: "From", kind: "date" },
        { key: "period_end", label: "To", kind: "date" },
        { key: "rating", label: "Rating out of five", kind: "integer" },
        { key: "strengths", label: "Strengths", kind: "textarea", wide: true },
        { key: "improvements", label: "To improve", kind: "textarea", wide: true },
        { key: "goals", label: "Goals for the next span", kind: "textarea", wide: true },
        { key: "employee_comment", label: "What they said", kind: "textarea", readOnly: true, existingOnly: true, wide: true },
      ]}
      actions={[
        { label: "Submit", path: "submit", permission: "hr.change_appraisal", when: is("draft"), primary: true, done: "Submitted" },
        { label: "Acknowledge", path: "acknowledge", permission: "hr.acknowledge_appraisal", when: is("submitted"), primary: true,
          done: "Acknowledged", fields: [{ key: "comment", label: "Your comment", kind: "textarea" }] },
      ]}
    />
  );
}
