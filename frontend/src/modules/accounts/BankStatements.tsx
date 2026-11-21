import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "bank_account_name", label: "Bank account" },
  { key: "reference", label: "Reference", width: "9rem" },
  { key: "start_date", label: "From", kind: "date", width: "8rem" },
  { key: "end_date", label: "To", kind: "date", width: "8rem" },
  { key: "closing_balance", label: "Closing balance", kind: "money", width: "11rem" },
  { key: "closed", label: "", width: "7rem", render: (row) => (row.closed ? "Closed" : "Open") },
];

export default function BankStatements() {
  return (
    <ListView<Row>
      title="Bank statements"
      noun={["statement", "statements"]}
      endpoint="/api/accounting/bank-statements/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/bank-statements/${row.id}`}
      searchHint="Reference or bank account"
      facets={[{ label: "Open", params: { closed: "false" } }, { label: "Closed", params: { closed: "true" } }]}
      create={{ href: "/accounts/bank-statements/new", permission: "accounting.add_bankstatement" }}
    />
  );
}
