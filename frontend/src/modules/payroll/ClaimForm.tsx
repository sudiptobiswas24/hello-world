import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { ACCOUNT } from "../purchasing/refs";

/** The expense accounts only: a claim line is never against the bank or a customer. */
const EXPENSE_ACCOUNT: NonNullable<FieldDef["pick"]> = {
  ...(ACCOUNT as NonNullable<FieldDef["pick"]>), query: { account_type: "expense" },
};
import { EMPLOYEE } from "../quality/refs";

type Row = Record<string, unknown> & { id: number };
const is = (...states: string[]) => (row: Row) => states.includes(String(row.status));
const TONE: Record<string, string> = { draft: "draft", submitted: "open", approved: "info", paid: "done", rejected: "draft" };

/**
 * One claim: the lines a person spent, submitted to their manager,
 * approved or rejected with a reason, and paid by accounts as one
 * journal from the cash or bank account chosen. Paid wrongly, the
 * payment is reversed and the claim stands approved again.
 */
export default function ClaimForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/expense-claims/"
      back="/payroll/claims"
      backLabel="Expense claims"
      newTitle="New expense claim"
      heading={(row) => `${String(row.number || "Claim")} · ${String(row.employee_name ?? "")}`}
      state={(row) => ({ label: String(row.status), tone: TONE[String(row.status)] ?? "draft" })}
      permissions={{ add: "hr.add_expenseclaim", change: "hr.change_expenseclaim", delete: "hr.delete_expenseclaim" }}
      editable={is("draft")}
      fields={[
        { key: "employee", label: "Who", kind: "pick", pick: EMPLOYEE, createOnly: true, hint: "Empty: yourself",
          show: (row) => String(row.employee_name ?? "") },
        { key: "claim_date", label: "Date", kind: "date", hint: "Empty: today" },
        { key: "purpose", label: "For", wide: true },
        { key: "total", label: "Comes to", kind: "money", readOnly: true, existingOnly: true },
        { key: "decision_note", label: "Decided", readOnly: true, existingOnly: true },
        { key: "decided_by_name", label: "Decided by", readOnly: true, existingOnly: true },
        { key: "paid_on", label: "Paid on", kind: "date", readOnly: true, existingOnly: true },
        { key: "paid_from_name", label: "Paid from", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Submit", path: "submit", permission: "hr.change_expenseclaim", when: is("draft"), primary: true, done: "Submitted" },
        { label: "Approve", path: "approve", permission: "hr.decide_expenseclaim", when: is("submitted"), primary: true, done: "Approved",
          fields: [{ key: "note", label: "Note", kind: "text" }] },
        { label: "Reject", path: "reject", permission: "hr.decide_expenseclaim", when: is("submitted"), danger: true, done: "Rejected",
          fields: [{ key: "note", label: "Why", kind: "text", hint: "Say why, or it comes back unchanged" }] },
        { label: "Pay", path: "pay", permission: "hr.pay_expenseclaim", when: is("approved"), primary: true, done: "Paid and posted",
          fields: [
            { key: "paid_from", label: "From account", kind: "pick", pick: ACCOUNT },
            { key: "on_date", label: "On", kind: "date", hint: "Empty: today" },
            { key: "memo", label: "Memo", kind: "text" },
          ] },
        { label: "Reverse the payment", path: "unpay", permission: "hr.pay_expenseclaim", when: is("paid"), danger: true,
          done: "Payment reversed", fields: [{ key: "reason", label: "Why", kind: "text" }] },
      ]}
      panels={[{
        title: "Lines", permission: "hr.view_expenseline", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "spent_on", label: "Spent on", kind: "date", width: "8rem" },
          { key: "description", label: "What" },
          { key: "expense_account_name", label: "Account" },
          { key: "amount", label: "Amount", kind: "money", width: "9rem" },
          { key: "receipt_reference", label: "Receipt", width: "9rem" },
        ],
        adder: { label: "Add a line", permission: "hr.add_expenseline", when: is("draft"),
          url: () => "/api/hr/expense-lines/",
          fields: [
            { key: "spent_on", label: "Spent on", kind: "date" },
            { key: "description", label: "What", kind: "text" },
            { key: "expense_account", label: "Account", kind: "pick", pick: EXPENSE_ACCOUNT },
            { key: "amount", label: "Amount", kind: "money" },
            { key: "receipt_reference", label: "Receipt", kind: "text" },
          ],
          body: (values, record) => ({ ...values, claim: record.id }) },
        remover: { permission: "hr.delete_expenseline", when: is("draft"), url: (row) => `/api/hr/expense-lines/${row.id}/` },
      }]}
    />
  );
}
