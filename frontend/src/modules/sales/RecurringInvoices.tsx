import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code", width: "8rem" },
  { key: "customer_name", label: "Customer" },
  { key: "interval", label: "Every", width: "8rem",
    render: (row) => `${Number(row.interval_count) > 1 ? `${String(row.interval_count)} × ` : ""}${String(row.interval)}` },
  { key: "next_run_date", label: "Next", kind: "date", width: "8rem" },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Stopped") },
];

export default function RecurringInvoices() {
  return (
    <ListView<Row>
      title="Recurring invoices"
      noun={["schedule", "schedules"]}
      endpoint="/api/sales/recurring-invoices/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/recurring/${row.id}`}
      searchHint="Code or customer"
      facets={[{ label: "Running", params: { is_active: "true" } }]}
      create={{ href: "/sales/recurring/new", permission: "sales.add_recurringinvoice" }}
      actions={[
        { label: "Issue every invoice now due", permission: "sales.add_invoice", path: "/api/sales/recurring-invoices/run/",
          confirm: "Issue the invoices every running schedule has due today?",
          done: (result) => `${Array.isArray(result) ? result.length : 0} invoices issued` },
      ]}
    />
  );
}
