import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "date", label: "Date", kind: "date", width: "8rem" },
  { key: "section_code", label: "Section", width: "6rem" },
  { key: "customer_name", label: "Customer" },
  { key: "invoice_number", label: "Invoice", width: "10rem" },
  { key: "amount", label: "Deducted", kind: "money", width: "9rem" },
  { key: "confirmed_on", label: "In Form 26AS", width: "9rem", render: (row) => (row.reversed ? "Reversed" : row.confirmed_on ? String(row.confirmed_on) : "Not yet") },
];

/** Tax customers deducted from paying: a claim on the government until Form 26AS shows it. */
export default function CustomerTdsList() {
  return (
    <ListView<Row>
      title="TDS by customers"
      noun={["deduction", "deductions"]}
      endpoint="/api/sales/customer-tds/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/customer-tds/${row.id}`}
      searchHint="Invoice, customer or certificate"
      facets={[{ label: "Not in 26AS yet", params: { confirmed_on__isnull: "true", reversed_entry__isnull: "true" } }]}
      create={{ href: "/accounts/customer-tds/new", permission: "sales.add_customertds" }}
    />
  );
}
