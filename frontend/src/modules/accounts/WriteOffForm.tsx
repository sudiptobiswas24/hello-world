import { Link } from "react-router";

import { RecordScreen } from "../../views/RecordScreen";

/**
 * One receivable given up on. Not changed: if the money comes after all,
 * the write-off is recovered, which reverses its entry and leaves it on
 * record, visibly reversed.
 */
export default function WriteOffForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/invoice-write-offs/"
      back="/accounts/bad-debts"
      backLabel="Bad debts"
      newTitle="Write-off"
      heading={(row) => `${String(row.invoice_number ?? "")} · ${String(row.customer_name ?? "")}`}
      state={(row) => (row.is_recovered ? { label: "Recovered", tone: "done" } : null)}
      permissions={{}}
      fields={[
        { key: "invoice", label: "Invoice", readOnly: true, show: (row) => <Link to={`/sales/invoices/${String(row.invoice)}`}>{String(row.invoice_number)}</Link> },
        { key: "amount", label: "Written off", kind: "money", readOnly: true },
        { key: "date", label: "On", kind: "date", readOnly: true },
        { key: "reason", label: "Why", readOnly: true },
      ]}
      actions={[
        { label: "Recover", path: "recover", permission: "sales.write_off_invoice", when: (row) => !row.is_recovered,
          done: "Recovered: the receivable is owed again",
          fields: [{ key: "date", label: "On", kind: "date" }] },
      ]}
    />
  );
}
