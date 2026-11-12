import { RecordScreen } from "../../views/RecordScreen";

/** One bank rate from the day it applies. A changed figure waits for the accountant's confirmation again. */
export default function BankRateForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/bank-rates/"
      back="/settings/bank-rates"
      backLabel="Bank rates"
      newTitle="New bank rate"
      heading={(row) => `Bank rate from ${String(row.applies_from ?? "")}`}
      state={(row) => (row.confirmed_on ? { label: "Confirmed", tone: "done" } : { label: "To confirm", tone: "draft" })}
      permissions={{ add: "accounting.add_bankrate", change: "accounting.change_bankrate", delete: "accounting.delete_bankrate" }}
      fields={[
        { key: "applies_from", label: "Applies from", kind: "date" },
        { key: "rate_percent", label: "Bank rate %", kind: "decimal", places: 4, hint: "As the Reserve Bank notified it, a year. MSME interest is three times this (MSMED Act s.16)" },
        { key: "authority", label: "Notification", hint: "The Reserve Bank's notification or press release it comes from" },
        { key: "confirmed_on", label: "Confirmed on", kind: "date", existingOnly: true, readOnly: true },
      ]}
      actions={[
        // The figures the accountant read, so a row changed since is refused, not confirmed unseen.
        { label: "Confirm", path: "confirm", body: (_values, record) => ({ applies_from: record.applies_from, rate_percent: record.rate_percent }),
          permission: "accounting.confirm_bankrate", when: (row) => !row.confirmed_on,
          done: "Confirmed: MSME interest is worked at it from its day" },
      ]}
    />
  );
}
