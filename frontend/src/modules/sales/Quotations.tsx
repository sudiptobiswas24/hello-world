import { ListView, type Column } from "../../views/ListView";

interface Quotation {
  id: number;
  number: string;
  revision: number;
  customer_name: string;
  quotation_date: string;
  valid_until: string | null;
  total: string;
  status: string;
}

const columns: Column<Quotation>[] = [
  { key: "number", label: "Number", sort: "number", width: "11rem",
    render: (row) => (row.number ? `${row.number}${row.revision > 1 ? ` (rev ${row.revision})` : ""}` : "Draft") },
  { key: "customer_name", label: "Customer" },
  { key: "quotation_date", label: "Date", kind: "date", sort: "quotation_date", width: "8rem" },
  { key: "valid_until", label: "Valid until", kind: "date", sort: "valid_until", width: "8rem" },
  { key: "total", label: "Total", kind: "money", width: "9rem" },
  { key: "status", label: "State", kind: "status", width: "8rem" },
];

export default function Quotations() {
  return (
    <ListView<Quotation>
      title="Quotations"
      noun={["quotation", "quotations"]}
      endpoint="/api/sales/quotations/"
      columns={columns}
      rowKey={(row) => row.id}
      create={{ href: "/sales/quotations/new", permission: "sales.add_quotation" }}
      rowHref={(row) => `/sales/quotations/${row.id}`}
      searchHint="Number, customer, reference"
      facets={[
        { label: "Drafts", params: { status: "draft" } },
        { label: "Awaiting answer", params: { status: "sent" } },
        { label: "Accepted", params: { status: "accepted" } },
        { label: "Declined", params: { status: "declined" } },
      ]}
    />
  );
}
