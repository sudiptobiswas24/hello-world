import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "valid_from", label: "From" },
  { key: "overhead_percent", label: "Overhead %", kind: "quantity" },
  { key: "margin_percent", label: "Margin %", kind: "quantity" },
  { key: "note", label: "Note" },
];

export default function QuotePolicies() {
  return (
    <ListView<Row>
      title="Quotation policy"
      noun={["quotation policy", "quotation policies"]}
      endpoint="/api/manufacturing/quote-policies/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/quote-policies/${row.id}`}
      searchHint="Note"
      create={{ href: "/making/quote-policies/new", permission: "manufacturing.add_quotepolicy" }}
    />
  );
}
