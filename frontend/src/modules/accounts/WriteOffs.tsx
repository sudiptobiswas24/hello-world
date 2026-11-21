import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "date", label: "Date", kind: "date", width: "8rem" },
  { key: "invoice_number", label: "Invoice", width: "10rem" },
  { key: "customer_name", label: "Customer" },
  { key: "reason", label: "Why" },
  { key: "amount", label: "Written off", kind: "money", width: "10rem" },
  { key: "is_recovered", label: "", width: "7rem", render: (row) => (row.is_recovered ? "Recovered" : "") },
];

/** Receivables given up on, each with its reason; written off from the invoice. */
export default function WriteOffs() {
  return (
    <ListView<Row>
      title="Bad debts"
      noun={["write-off", "write-offs"]}
      endpoint="/api/sales/invoice-write-offs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/bad-debts/${row.id}`}
      searchHint="Invoice, customer or reason"
    />
  );
}
