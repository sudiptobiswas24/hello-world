import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "account_label", label: "Account" },
  { key: "start_date", label: "From", kind: "date" },
  { key: "end_date", label: "To", kind: "date" },
  { key: "amount", label: "Amount", kind: "money" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function Budgets() {
  return (
    <ListView<Row>
      title="Budgets"
      noun={["budget", "budgets"]}
      endpoint="/api/purchasing/budgets/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/budgets/${row.id}`}
      searchHint="Code, name or account"
      create={{ href: "/purchasing/budgets/new", permission: "purchasing.add_budget" }}
    />
  );
}
