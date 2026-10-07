import { RecordScreen } from "../../views/RecordScreen";
import { ACCOUNT, COST_CENTRE, PARTY } from "./refs";

type Row = Record<string, unknown>;

/**
 * One schedule: what the entry says, how often, from when; its lines, which
 * must balance; and the entries taken from it so far. A draft each time
 * unless the schedule posts, which then takes the right to post.
 */
export default function RecurringJournalForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/recurring-journals/"
      trail="accounting.recurringjournal"
      back="/accounts/recurring-journals"
      backLabel="Recurring journals"
      newTitle="New recurring journal"
      heading={(row) => `${String(row.code ?? "")} · ${String(row.memo ?? "")}`}
      state={(row) => (row.is_active === false ? { label: "Stopped", tone: "draft" } : { label: "Running", tone: "open" })}
      permissions={{ add: "accounting.add_recurringjournal", change: "accounting.change_recurringjournal", delete: "accounting.delete_recurringjournal" }}
      fields={[
        { key: "code", label: "Code", hint: "RENT, INS" },
        { key: "memo", label: "What each entry is for", wide: true },
        { key: "interval", label: "Every", kind: "choice", choices: [["weekly", "Week"], ["monthly", "Month"], ["quarterly", "Quarter"], ["yearly", "Year"]], initial: "monthly" },
        { key: "interval_count", label: "Intervals between", kind: "integer", initial: 1, hint: "2 with Month: every other month" },
        { key: "start_date", label: "Starts", kind: "date", hint: "The series keeps this day of the month" },
        { key: "end_date", label: "Ends", kind: "date", hint: "Empty: until stopped" },
        { key: "next_run_date", label: "Next entry", readOnly: true, kind: "date", existingOnly: true },
        { key: "auto_post", label: "Post each entry", kind: "bool", hint: "Otherwise each is left a draft to check" },
        { key: "is_active", label: "Running", kind: "bool", initial: true },
      ]}
      actions={[
        { label: "Take the next entry", path: "generate", permission: "accounting.add_journalentry", primary: true,
          when: (row) => Boolean(row.is_active), done: "Entry taken", then: (entry) => `/accounts/journals/${String(entry.id)}` },
      ]}
      panels={[
        {
          title: "Lines", permission: "accounting.view_recurringjournalline", endpoint: "/api/accounting/recurring-journal-lines/",
          query: (schedule) => ({ schedule: String(schedule.id) }),
          columns: [
            { key: "account_code", label: "Account", width: "6rem" },
            { key: "account_name", label: "" },
            { key: "party_name", label: "Party" },
            { key: "description", label: "Line" },
            { key: "cost_centre_name", label: "Centre", width: "8rem" },
            { key: "debit", label: "Debit", kind: "money", width: "9rem" },
            { key: "credit", label: "Credit", kind: "money", width: "9rem" },
          ],
          adder: {
            label: "Add a line", permission: "accounting.add_recurringjournalline", url: () => "/api/accounting/recurring-journal-lines/",
            fields: [
              { key: "account", label: "Account", kind: "pick", pick: ACCOUNT },
              { key: "debit", label: "Debit", kind: "money" },
              { key: "credit", label: "Credit", kind: "money" },
              { key: "cost_centre", label: "Centre", kind: "pick", pick: COST_CENTRE },
              { key: "party", label: "Party", kind: "pick", pick: PARTY },
              { key: "description", label: "Line" },
            ],
            body: (values: Row, schedule: Row) => ({ ...values, schedule: schedule.id }),
          },
          remover: { permission: "accounting.delete_recurringjournalline", url: (row) => `/api/accounting/recurring-journal-lines/${String(row.id)}/` },
        },
        {
          title: "Entries taken", permission: "accounting.view_journalentry", endpoint: "/api/accounting/journal-entries/",
          query: (schedule) => ({ recurring_journal: String(schedule.id) }),
          href: (row) => `/accounts/journals/${String(row.id)}`,
          columns: [
            { key: "date", label: "Date", kind: "date", width: "8rem" },
            { key: "reference", label: "Reference", width: "9rem" },
            { key: "memo", label: "What" },
            { key: "posted", label: "State", width: "7rem", render: (row: Row) => (row.posted ? "Posted" : "Draft") },
          ],
        },
      ]}
    />
  );
}
