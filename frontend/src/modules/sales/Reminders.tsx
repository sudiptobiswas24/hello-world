import { dateTime } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "sent_at", label: "Sent", width: "11rem", render: (row) => (row.sent_at ? dateTime(String(row.sent_at)) : "Not sent") },
  { key: "invoice_number", label: "Invoice", width: "10rem" },
  { key: "customer_name", label: "Customer" },
  { key: "level_name", label: "Level", width: "10rem" },
  { key: "days_overdue", label: "Days late", kind: "quantity", width: "7rem" },
  { key: "amount_due", label: "Due", kind: "money", width: "9rem" },
  { key: "sent_to", label: "To" },
];

/**
 * Reminders raised for overdue invoices, and the two runs that write to
 * every customer: one reminder per level reached, and statements.
 */
export default function Reminders() {
  return (
    <ListView<Row>
      title="Payment reminders"
      noun={["reminder", "reminders"]}
      endpoint="/api/sales/dunning-notices/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Invoice, customer or address"
      actions={[
        { label: "Send reminders now", permission: "sales.post_invoice", path: "/api/sales/dunning-levels/run/",
          confirm: "Email every customer whose invoices have reached a reminder level?",
          done: (result) => `${Array.isArray(result) ? result.length : 0} reminders raised` },
        { label: "Send statements", permission: "sales.post_invoice", path: "/api/sales/sales-reports/statements/",
          confirm: "Email every customer their statement?",
          done: (result) => `${String((result as { sent?: number }).sent ?? 0)} statements sent` },
      ]}
    />
  );
}
