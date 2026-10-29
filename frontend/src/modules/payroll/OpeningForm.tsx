import { RecordScreen } from "../../views/RecordScreen";
import { DEPARTMENT, SOURCES } from "./peopleRefs";

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));

/** One opening: what is wanted and how many, and the people who applied, each on their own page. */
export default function OpeningForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/job-openings/"
      back="/payroll/openings"
      backLabel="Job openings"
      newTitle="New job opening"
      heading={(row) => String(row.title ?? "Opening")}
      state={(row) => ({ label: String(row.status).replace("_", " "), tone: row.status === "open" ? "open" : row.status === "filled" ? "done" : "draft" })}
      permissions={{ add: "hr.add_jobopening", change: "hr.change_jobopening", delete: "hr.delete_jobopening" }}
      editable={is("open", "on_hold")}
      fields={[
        { key: "title", label: "Job", wide: true },
        { key: "department", label: "Department", kind: "ref", ref: DEPARTMENT },
        { key: "openings", label: "How many", kind: "integer", initial: 1 },
        { key: "opened_on", label: "Opened on", kind: "date", hint: "Empty: today" },
        { key: "description", label: "What the job is", kind: "textarea", wide: true },
        { key: "closed_on", label: "Closed on", kind: "date", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Put on hold", path: "hold", permission: "hr.change_jobopening", when: is("open"), done: "On hold" },
        { label: "Reopen", path: "reopen", permission: "hr.change_jobopening", when: is("on_hold", "closed"), done: "Open again" },
        { label: "Close", path: "close", permission: "hr.change_jobopening", when: is("open", "on_hold"), danger: true, done: "Closed" },
      ]}
      panels={[{
        title: "Applicants", permission: "hr.view_applicant", endpoint: "/api/hr/applicants/",
        query: (opening) => ({ opening: String(opening.id) }),
        href: (row) => `/payroll/applicants/${row.id}`,
        columns: [
          { key: "name", label: "Who" },
          { key: "applied_on", label: "Applied", kind: "date", width: "8rem" },
          { key: "source", label: "From", kind: "status", width: "8rem" },
          { key: "stage", label: "Stage", kind: "status", width: "8rem" },
          { key: "rating", label: "Rating", width: "6rem", render: (row) => (row.rating == null ? "—" : `${String(row.rating)} / 5`) },
        ],
        adder: { label: "Add an applicant", permission: "hr.add_applicant", when: is("open"),
          url: () => "/api/hr/applicants/",
          fields: [
            { key: "name", label: "Who", kind: "text" },
            { key: "phone", label: "Phone", kind: "text" },
            { key: "email", label: "Email", kind: "text" },
            { key: "source", label: "From", kind: "choice", choices: SOURCES },
            { key: "applied_on", label: "Applied on", kind: "date" },
            { key: "expected_pay", label: "Expects a month", kind: "money" },
          ],
          body: (values, record) => ({ ...values, opening: record.id }) },
      }]}
    />
  );
}
