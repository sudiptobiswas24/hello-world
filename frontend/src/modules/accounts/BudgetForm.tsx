import { RecordScreen } from "../../views/RecordScreen";
import { ACCOUNT, COST_CENTRE } from "./refs";

type Row = Record<string, unknown>;

/**
 * One budget: its span, and a line per income or expense account — with a
 * centre where it matters who spends it. A line without a centre is the
 * account's whole. "Against the books" reads it against what posted.
 */
export default function BudgetForm() {
  return (
    <RecordScreen
      endpoint="/api/accounting/budgets/"
      trail="accounting.budget"
      back="/accounts/budgets"
      backLabel="Budgets"
      newTitle="New budget"
      heading={(row) => `${String(row.code ?? "")} · ${String(row.name ?? "")}`}
      state={(row) => (row.is_active === false ? { label: "Retired", tone: "draft" } : null)}
      permissions={{ add: "accounting.add_budget", change: "accounting.change_budget", delete: "accounting.delete_budget" }}
      fields={[
        { key: "code", label: "Code", hint: "FY27, Q1FY27" },
        { key: "name", label: "Name" },
        { key: "start_date", label: "From", kind: "date" },
        { key: "end_date", label: "To", kind: "date" },
        { key: "note", label: "Note", wide: true },
        { key: "is_active", label: "Current", kind: "bool", initial: true },
      ]}
      links={[{ label: "Against the books", same: true, href: (row) => `/accounts/budget-report?budget=${String(row.id)}` }]}
      panels={[
        {
          title: "Lines", permission: "accounting.view_budgetline", endpoint: "/api/accounting/budget-lines/",
          query: (budget) => ({ budget: String(budget.id) }),
          columns: [
            { key: "account_code", label: "Account", width: "7rem" },
            { key: "account_name", label: "" },
            { key: "cost_centre_name", label: "Centre", render: (row: Row) => String(row.cost_centre_name || "Whole account") },
            { key: "amount", label: "For the span", kind: "money", width: "12rem" },
          ],
          adder: {
            label: "Add a line", permission: "accounting.add_budgetline", url: () => "/api/accounting/budget-lines/",
            fields: [
              { key: "account", label: "Account", kind: "pick", pick: ACCOUNT, hint: "An income or expense account" },
              { key: "cost_centre", label: "Centre", kind: "pick", pick: COST_CENTRE, hint: "Empty: the account's whole" },
              { key: "amount", label: "Amount", kind: "money", hint: "For the whole span" },
            ],
            body: (values, budget) => ({ ...values, budget: budget.id }),
          },
          remover: { permission: "accounting.delete_budgetline", url: (row) => `/api/accounting/budget-lines/${String(row.id)}/` },
        },
      ]}
    />
  );
}
