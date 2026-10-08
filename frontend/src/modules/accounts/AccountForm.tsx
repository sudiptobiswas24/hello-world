import { useAccess } from "../../auth/me";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { CURRENCY } from "./refs";

type Row = Record<string, unknown>;

const KINDS: [string, string][] = [
  ["asset", "Asset"], ["liability", "Liability"], ["equity", "Equity"], ["income", "Income"], ["expense", "Expense"],
];

/** What an account may sit under: an account of its own kind. */
const under = (kind: unknown): FieldDef["pick"] => ({
  endpoint: "/api/accounting/accounts/", permission: "accounting.view_account",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`, query: { account_type: String(kind ?? "") },
});

const MONEY = "What payments, claims and challans are paid from or into; nothing else is offered for that. "
  + "An asset, or a liability for a card or an overdraft. It cannot change once anything is posted to the account.";

/**
 * One account of the chart: made from the chart, and changed from its
 * ledger, which stays its page. What the books cannot take (a parent of
 * another kind, a kind or a money mark changed once posted to) the
 * server refuses, beside the box it is about.
 */
export default function AccountForm() {
  const { can } = useAccess();
  return (
    <RecordScreen
      endpoint="/api/accounting/accounts/"
      trail="accounting.account"
      back="/accounts/chart"
      backLabel="Chart of accounts"
      newTitle="New account"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      permissions={{ add: "accounting.add_account", change: "accounting.change_account", delete: "accounting.delete_account" }}
      links={[{ label: "Ledger", same: true, href: (row) => `/accounts/chart/${String(row.id)}`,
        when: () => can("accounting.view_journalentry") }]}
      fields={(value) => [
        { key: "code", label: "Code", hint: "Its number in the chart: 1010, 2300" },
        { key: "name", label: "Name" },
        { key: "account_type", label: "Kind", kind: "choice", choices: KINDS,
          hint: "Which statement reports it. It cannot change once anything is posted to it." },
        { key: "parent", label: "Parent", kind: "pick", pick: under(value.account_type),
          hint: "An account of the same kind it is grouped under; empty for one at the top",
          show: (row) => (row.parent ? `${String(row.parent_code)} · ${String(row.parent_name)}` : "—") },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY },
        { key: "is_active", label: "In use", kind: "bool", initial: true },
        { key: "holds_money", label: "Bank, cash or card", kind: "bool", hint: MONEY },
      ]}
    />
  );
}
