import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { MONEY_ACCOUNT } from "./refs";

type Row = Record<string, unknown>;

/** A posted payment through the account, from before its cut-off. */
const payment = (value: Row): FieldDef["pick"] => ({
  endpoint: "/api/accounting/payments/", permission: "accounting.view_payment",
  query: { bank_account: String(value.bank_account ?? ""), posted: "true" },
  label: (row: Row) => `${String(row.number)} · ${String(row.party_name)} · ${String(row.payment_date)} · ${String(row.amount)}`,
});
const ENTRY: FieldDef["pick"] = {
  endpoint: "/api/accounting/journal-entries/", permission: "accounting.view_journalentry", query: { posted: "true" },
  label: (row: Row) => `JE-${String(row.id)} · ${String(row.date)} · ${String(row.memo ?? "")}`,
};
const settled = (row: Row) => Boolean(row.cancelled_by || row.shown_on);

/**
 * One thing outstanding at a bank's cut-off (O615): a payment or an entry
 * from before the day the account is reconciled from, or a cheque from
 * before the books opened, inside their opening balance. The server fills
 * a payment's or an entry's own day and amount, and refuses any change
 * while a statement of the account is closed.
 */
export default function BankCutOffItemForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/bank-cut-off-items/"
      back="/accounts/bank-cut-off"
      backLabel="Outstanding at a bank's cut-off"
      newTitle="Outstanding at the cut-off"
      heading={(row) => `${String(row.bank_account_name ?? "")}: ${String(row.reference || row.description || "")}`}
      state={(row) => (row.cancelled_by ? { label: "Cancelled", tone: "done" }
        : row.shown_on ? { label: "Shown", tone: "done" } : { label: "Outstanding", tone: "draft" })}
      permissions={{ add: "accounting.add_bankcutoffitem", change: "accounting.change_bankcutoffitem",
        delete: "accounting.delete_bankcutoffitem" }}
      editable={(row) => !settled(row)}
      fields={(value) => [
        { key: "bank_account", label: "Bank account", kind: "pick", pick: MONEY_ACCOUNT, createOnly: true,
          hint: "Set the day it is reconciled from on the account first" },
        { key: "payment", label: "Payment", kind: "pick", pick: payment(value),
          hint: "A payment from before the cut-off the bank had not shown by then; leave empty for a cheque from before the books" },
        { key: "entry", label: "Entry", kind: "pick", pick: ENTRY,
          hint: "Or an entry through the bank from before the cut-off: a journal keyed by hand, a challan paid over" },
        { key: "date", label: "Written", kind: "date", hint: "A cheque's own day; a payment's or an entry's is filled in" },
        { key: "reference", label: "Reference", hint: "The cheque's number" },
        { key: "description", label: "Description", hint: "Who it was to or from" },
        { key: "amount", label: "Amount", kind: "money", negative: true,
          hint: "As the bank will show it: a cheque issued is negative. A payment's or an entry's is filled in" },
      ]}
      actions={[
        { label: "Cancel it", path: "cancel", permission: "accounting.change_bankcutoffitem",
          when: (row) => !settled(row) && !row.payment && !row.entry, done: "Cancelled",
          fields: () => [{ key: "entry", label: "By the entry", kind: "pick", pick: ENTRY,
            hint: "The journal that put the cheque's money back, dated on or after the cut-off" }] },
        { label: "Take the cancellation back", path: "uncancel", permission: "accounting.change_bankcutoffitem",
          when: (row) => Boolean(row.cancelled_by), done: "Outstanding again" },
      ]}
    />
  );
}
