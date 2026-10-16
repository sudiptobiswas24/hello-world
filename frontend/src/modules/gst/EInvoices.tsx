import { dateTime } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "invoice_number", label: "Invoice", width: "10rem" },
  { key: "irn", label: "IRN", render: (row) => (row.irn ? `${String(row.irn).slice(0, 16)}…` : "Not registered yet") },
  { key: "ack_number", label: "Ack no.", width: "10rem" },
  { key: "ack_date", label: "Acknowledged", width: "11rem", render: (row) => (row.ack_date ? dateTime(String(row.ack_date)) : "") },
];

export default function EInvoices() {
  return (
    <ListView<Row>
      title="E-invoices"
      noun={["e-invoice", "e-invoices"]}
      endpoint="/api/gst/e-invoices/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/e-invoices/${row.id}`}
      searchHint="Invoice or IRN"
      create={{ href: "/accounts/e-invoices/new", permission: "gst.add_einvoice" }}
    />
  );
}
