import { ListView, type Column } from "../../views/ListView";

interface Account {
  id: number;
  code: string;
  name: string;
  account_type: string;
  is_active: boolean;
  /** A bank, cash box, card or overdraft: what money is paid from or into. */
  holds_money: boolean;
}

const columns: Column<Account>[] = [
  { key: "code", label: "Code", sort: "code", width: "8rem" },
  { key: "name", label: "Name", sort: "name" },
  { key: "account_type", label: "Kind", width: "11rem", render: (row) => `${row.account_type.replace(/_/g, " ")}${row.holds_money ? " · money" : ""}` },
  { key: "is_active", label: "State", width: "7rem", render: (row) => (row.is_active ? "In use" : "Closed") },
];

/** The chart of accounts; each opens on its ledger. */
export default function Chart() {
  return (
    <ListView<Account>
      title="Chart of accounts"
      noun={["account", "accounts"]}
      endpoint="/api/accounting/accounts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/chart/${row.id}`}
      searchHint="Code or name"
      facets={[
        { label: "Assets", params: { account_type: "asset" } },
        { label: "Liabilities", params: { account_type: "liability" } },
        { label: "Income", params: { account_type: "income" } },
        { label: "Expenses", params: { account_type: "expense" } },
        { label: "Bank, cash and cards", params: { holds_money: "true" } },
      ]}
    />
  );
}
