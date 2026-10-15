import { money } from "../../lib/format";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;
type Transfer = { id: number; employee: string; holder: string; amount: string; returned: boolean };

const PAYMENT: FieldDef["pick"] = { endpoint: "/api/accounting/payments/", permission: "accounting.view_payment", label: (row: Row) => `${String(row.number)} · ${money(row.amount as string)}` };

/**
 * One run's bank file: what was sent to the bank, the one payment out that paid it, and a transfer
 * the bank sent back, which unpays that slip alone. Paying and returning are the ledger's side
 * (hr.post_payrun), as the server asks; whoever works the payroll out writes the file.
 */
export default function SalaryFileForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/salary-files/"
      back="/payroll/salary-files"
      backLabel="Bank files"
      newTitle="Bank file"
      heading={(row) => `Bank file ${String(row.number ?? "")}`}
      // Its real state, from the server (review RP6): a voided payment paid nobody, and this file is not paid again.
      state={(row) => (row.paid ? { label: "Paid", tone: "good" }
        : row.payment_voided ? { label: "Payment voided", tone: "bad" } : { label: "Not paid", tone: "warn" })}
      note={(row) => (row?.payment_voided
        ? "Not paid: the payment that paid this file was voided, so nobody on it is paid. Write the file again from the pay run."
        : null)}
      permissions={{ delete: "hr.delete_salarytransferfile" }}
      editable={() => false}
      fields={[
        { key: "narration", label: "Narration" },
        { key: "total", label: "Total", kind: "money" },
        { key: "paid_at", label: "Marked paid", kind: "date" },
      ]}
      links={[{ label: "CSV for the bank", href: (row) => `/api/hr/salary-files/${String(row.id)}/csv/`, same: true }]}
      actions={[
        { label: "Mark paid", path: "mark_paid", permission: "hr.post_payrun", primary: true, when: (row) => !row.payment,
          done: "Marked paid", fields: [{ key: "payment", label: "Paid by", kind: "pick", pick: PAYMENT }] },
        { label: "Return a transfer", path: "return_transfer", permission: "hr.post_payrun", danger: true,
          when: (row) => Boolean(row.paid), done: "Returned",
          fields: (record) => [
            { key: "row", label: "Transfer", kind: "choice", choices: ((record.rows as Transfer[] | undefined) ?? [])
              .filter((row) => !row.returned).map((row) => [String(row.id), `${row.employee} · ${row.holder} · ${money(row.amount)}`]) },
            { key: "receipt", label: "Received back by", kind: "pick", pick: PAYMENT },
          ] },
      ]}
      panels={[{
        title: "Transfers", permission: "hr.view_salarytransfer", endpoint: "/api/hr/salary-files/", query: () => ({}),
        rows: (record) => (record.rows ?? []) as never,
        columns: [
          { key: "employee", label: "Employee", width: "8rem" },
          { key: "holder", label: "Account holder" },
          { key: "account_number", label: "Account", width: "11rem" },
          { key: "ifsc", label: "IFSC", width: "8rem" },
          { key: "amount", label: "Amount", kind: "money", width: "9rem" },
          { key: "returned", label: "Sent back", width: "7rem", render: (row) => (row.returned ? "Returned" : "") },
        ],
      }]}
    />
  );
}
