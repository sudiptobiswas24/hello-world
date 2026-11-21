import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;
type Line = Row & { id: number };

const FIGURES: [string, string][] = [["amount", "Budget"], ["spent", "Billed"], ["committed", "Ordered, not billed"], ["requested", "Asked for, not ordered"], ["available", "Left"]];

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** What may be spent on an expense account over a period, against what is already billed, ordered and asked for. */
export default function BudgetForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/budgets/"
      back="/purchasing/budgets"
      backLabel="Budgets"
      newTitle="New budget"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "purchasing.add_budget", change: "purchasing.change_budget", delete: "purchasing.delete_budget" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "account", label: "Account", kind: "pick", pick: ACCOUNT, hint: "The expense account this budget governs" },
        { key: "start_date", label: "From", kind: "date" },
        { key: "end_date", label: "To", kind: "date" },
        { key: "amount", label: "Amount", kind: "money" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Against it", permission: "purchasing.view_budget", endpoint: "", query: () => ({}),
        read: {
          path: (budget) => `/api/purchasing/budgets/${String(budget.id)}/figures/`,
          rows: (data) => FIGURES.map(([key, label], index): Line => ({ id: index, label, amount: (data as Row)[key] })),
        },
        columns: [
          { key: "label", label: "" },
          { key: "amount", label: "Amount", kind: "money", width: "12rem" },
        ],
      }]}
    />
  );
}
