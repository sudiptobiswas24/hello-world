import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "code", label: "Code", sort: "code", width: "9rem" },
  { key: "name", label: "Name", sort: "name" },
  { key: "start_date", label: "From", kind: "date", sort: "start_date", width: "8rem" },
  { key: "end_date", label: "To", kind: "date", width: "8rem" },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Retired") },
];

/** What the plant agreed to spend and earn over a span, by account and centre; each one reads against the books. */
export default function Budgets() {
  return (
    <ListView<Row>
      title="Budgets"
      endpoint="/api/accounting/budgets/"
      columns={COLUMNS}
      rowHref={(row) => `/accounts/budgets/${row.id}`}
      searchHint="Code or name"
      noun={["budget", "budgets"]}
      create={{ href: "/accounts/budgets/new", permission: "accounting.add_budget" }}
      facets={[{ label: "Current", params: { is_active: "true" } }]}
    />
  );
}
