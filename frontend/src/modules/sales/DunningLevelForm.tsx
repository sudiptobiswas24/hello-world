import { RecordScreen } from "../../views/RecordScreen";

/** A payment reminder: how many days overdue it goes out at, and what it says. */
export default function DunningLevelForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/dunning-levels/"
      back="/sales/dunning-levels"
      backLabel="Reminder levels"
      newTitle="New reminder level"
      heading={(row) => String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "sales.add_dunninglevel", change: "sales.change_dunninglevel", delete: "sales.delete_dunninglevel" }}
      fields={[
        { key: "name", label: "Name" },
        { key: "days_overdue", label: "Days overdue", kind: "integer", hint: "Send once the invoice is at least this many days past due" },
        { key: "subject", label: "Subject", initial: "Reminder: invoice {number} is overdue", hint: "Supports {number}, {customer}, {days}, {amount}" },
        { key: "body", label: "Body", kind: "textarea", initial: "Dear {customer},\n\nInvoice {number} for {amount} was due on {due_date} and is now {days} days overdue.\n\nPlease arrange payment.\n", hint: "Supports {number}, {customer}, {days}, {amount}, {due_date}" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
