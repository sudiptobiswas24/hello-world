import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { ACCOUNT } from "../purchasing/refs";
import { EMPLOYEE } from "../quality/refs";

/** What time is charged against, for cost analysis: the expense accounts. */
const COST_ACCOUNT: NonNullable<FieldDef["pick"]> = {
  ...(ACCOUNT as NonNullable<FieldDef["pick"]>), query: { account_type: "expense" },
};

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));
const TONE: Record<string, string> = { draft: "draft", submitted: "open", approved: "done", rejected: "void" };

/**
 * One timesheet: the hours a person worked over a stretch of days, written
 * while a draft, submitted to their manager and approved (what a pay run
 * reads) or rejected. A submitted sheet is withdrawn by its person; a
 * decided one is sent back by whoever decides, and not once a posted run
 * has paid its hours.
 */
export default function TimesheetForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/timesheets/"
      back="/payroll/timesheets"
      backLabel="Timesheets"
      newTitle="New timesheet"
      heading={(row) => `Timesheet · ${String(row.employee_name ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: TONE[String(row.status)] ?? "draft" })}
      permissions={{ add: "hr.add_timesheet", change: "hr.change_timesheet", delete: "hr.delete_timesheet" }}
      editable={is("draft")}
      fields={[
        { key: "employee", label: "Who", kind: "pick", pick: EMPLOYEE, createOnly: true, hint: "Empty: yourself",
          show: (row) => String(row.employee_name ?? "") },
        { key: "period_start", label: "From", kind: "date" },
        { key: "period_end", label: "To", kind: "date" },
        { key: "note", label: "Note", wide: true },
        { key: "hours", label: "Hours", kind: "decimal", readOnly: true, existingOnly: true },
        { key: "billable_hours", label: "Billable hours", kind: "decimal", readOnly: true, existingOnly: true },
        { key: "decided_by_name", label: "Decided by", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Submit", path: "submit", permission: "hr.change_timesheet", when: is("draft"), primary: true, done: "Submitted" },
        { label: "Withdraw", path: "withdraw", permission: "hr.change_timesheet", when: is("submitted"), done: "Withdrawn" },
        { label: "Approve", path: "approve", permission: "hr.decide_timesheet", when: is("submitted"), primary: true, done: "Approved" },
        { label: "Reject", path: "reject", permission: "hr.decide_timesheet", when: is("submitted"), danger: true, done: "Rejected",
          fields: [{ key: "note", label: "Why", kind: "text", hint: "Say why, or it comes back unchanged" }] },
        { label: "Send back", path: "send_back", permission: "hr.decide_timesheet", when: is("approved", "rejected"), danger: true,
          done: "Sent back", fields: [{ key: "note", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Hours", permission: "hr.view_timesheetentry", endpoint: "", query: () => ({}),
        rows: (record) => (record.entries as Row[]) ?? [],
        columns: [
          { key: "date", label: "Day", kind: "date", width: "8rem" },
          { key: "hours", label: "Hours", kind: "quantity", width: "6rem" },
          { key: "account_name", label: "Charged to" },
          { key: "reference", label: "For" },
          { key: "is_billable", label: "Billable", width: "6rem", render: (row) => (row.is_billable ? "Yes" : "") },
          { key: "note", label: "Note" },
        ],
        adder: { label: "Add hours", permission: "hr.add_timesheetentry", when: is("draft"),
          url: () => "/api/hr/timesheet-entries/",
          fields: [
            { key: "date", label: "Day", kind: "date" },
            { key: "hours", label: "Hours", kind: "decimal" },
            { key: "account", label: "Charged to", kind: "pick", pick: COST_ACCOUNT },
            { key: "reference", label: "For", kind: "text", hint: "Job, ticket or customer" },
            { key: "is_billable", label: "Billable", kind: "bool", initial: false },
            { key: "note", label: "Note", kind: "text" },
          ],
          body: (values, record) => ({ ...values, timesheet: record.id }) },
        remover: { permission: "hr.delete_timesheetentry", when: is("draft"), url: (row) => `/api/hr/timesheet-entries/${row.id}/` },
      }]}
    />
  );
}
