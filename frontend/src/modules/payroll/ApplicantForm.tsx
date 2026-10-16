import { RecordScreen } from "../../views/RecordScreen";
import { OPENING, SOURCES } from "./peopleRefs";

type Row = Record<string, unknown> & { id: number };
const at = (...stages: string[]) => (row: Row) => stages.includes(String(row.stage));
const open = at("applied", "screening", "interview", "offered");
const NEXT: [string, string][] = [["screening", "Screening"], ["interview", "Interview"], ["offered", "Offered"]];

/**
 * One applicant: forward through screening, interview and offer, then
 * hired (which makes the employee record) or rejected with a reason
 * kept for the next time they apply.
 */
export default function ApplicantForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/applicants/"
      back="/payroll/applicants"
      backLabel="Applicants"
      newTitle="New applicant"
      heading={(row) => `${String(row.name ?? "Applicant")} · ${String(row.opening_title ?? "")}`}
      state={(row) => ({ label: String(row.stage), tone: row.stage === "hired" ? "done" : row.stage === "rejected" ? "draft" : "open" })}
      permissions={{ add: "hr.add_applicant", change: "hr.change_applicant", delete: "hr.delete_applicant" }}
      editable={open}
      fields={[
        { key: "opening", label: "For", kind: "pick", pick: OPENING, createOnly: true, show: (row) => String(row.opening_title ?? "") },
        { key: "name", label: "Who" },
        { key: "phone", label: "Phone" },
        { key: "email", label: "Email" },
        { key: "applied_on", label: "Applied on", kind: "date", hint: "Empty: today" },
        { key: "source", label: "From", kind: "choice", choices: SOURCES, initial: "walk_in" },
        { key: "rating", label: "Rating out of five", kind: "integer" },
        { key: "expected_pay", label: "Expects a month", kind: "money" },
        { key: "notes", label: "Notes", kind: "textarea", wide: true },
        { key: "rejected_reason", label: "Rejected because", readOnly: true, existingOnly: true },
        { key: "employee_number", label: "Employee number", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Move on", path: "advance", permission: "hr.change_applicant", when: at("applied", "screening", "interview"), primary: true,
          done: "Moved on", fields: [{ key: "stage", label: "To", kind: "choice", choices: NEXT }] },
        { label: "Hire", path: "hire", permission: "hr.add_employee", when: at("offered"), primary: true, done: "Hired: the employee record is made",
          fields: [
            { key: "employee_number", label: "Employee number", kind: "text" },
            { key: "hire_date", label: "Joins on", kind: "date", hint: "Empty: today" },
            { key: "job_title", label: "Job title", kind: "text", hint: "Empty: the opening's" },
          ], then: (made) => `/payroll/employees/${String(made.employee)}` },
        { label: "Reject", path: "reject", permission: "hr.change_applicant", when: open, danger: true, done: "Rejected",
          fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
    />
  );
}
