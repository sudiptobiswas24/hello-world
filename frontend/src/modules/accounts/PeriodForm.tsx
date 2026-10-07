import { RecordScreen } from "../../views/RecordScreen";

/**
 * One period of the books. Closed, nothing further posts into it, its
 * dates stay, and it cannot be deleted; reopening is a decision with its
 * own right, and the note says why.
 */
export default function PeriodForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/periods/"
      trail="accounting.accountingperiod"
      back="/accounts/periods"
      backLabel="Accounting periods"
      newTitle="New period"
      heading={(row) => String(row.name ?? "")}
      state={(row) => (row.closed ? { label: "Closed", tone: "done" } : { label: "Open", tone: "draft" })}
      permissions={{ add: "accounting.add_accountingperiod", change: "accounting.change_accountingperiod", delete: "accounting.delete_accountingperiod" }}
      fields={[
        { key: "name", label: "Name", hint: "Apr 2026, FY 2026-27" },
        { key: "start_date", label: "From", kind: "date" },
        { key: "end_date", label: "To", kind: "date" },
        { key: "note", label: "Note", wide: true, hint: "Why it was closed or reopened, and by whose sign-off" },
        { key: "closed_by_name", label: "Closed by", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Close the period", path: "close", permission: "accounting.close_accountingperiod", primary: true,
          when: (row) => !row.closed, done: "Closed: nothing posts into it now",
          fields: [{ key: "note", label: "Note", hint: "Signed off by whom" }] },
        { label: "Reopen", path: "reopen", permission: "accounting.close_accountingperiod", danger: true,
          when: (row) => Boolean(row.closed), done: "Reopened",
          fields: [{ key: "note", label: "Why", hint: "What has to be posted into it" }] },
      ]}
    />
  );
}
