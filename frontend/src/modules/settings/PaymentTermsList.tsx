import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "net_days", label: "Net days", kind: "quantity" },
  { key: "discount_percent", label: "Discount %", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function PaymentTermsList() {
  return (
    <ListView<Row>
      title="Payment terms"
      noun={["payment term", "payment terms"]}
      endpoint="/api/core/payment-terms/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/payment-terms/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/payment-terms/new", permission: "core.add_paymentterms" }}
    />
  );
}
