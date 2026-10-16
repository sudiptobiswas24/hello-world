import { money } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: string };
interface Totals { amount: string; to_date: string; actual: string; unbudgeted: string }
interface Report {
  code: string; name: string; start: string; end: string; as_of: string; days: number; elapsed: number;
  rows: Row[]; totals: { expense: Totals; income: Totals };
}

const PARAMS: ParamDef[] = [
  { key: "budget", label: "Budget", kind: "ref", required: true,
    ref: { endpoint: "/api/accounting/budgets/", permission: "accounting.view_budget",
      label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.id) } },
  { key: "as_of", label: "As of", kind: "date", initial: "today" },
];

const state = (row: Row) => row.unbudgeted ? <span className="pill pill-warn">Unbudgeted</span>
  : row.over ? <span className="pill pill-danger">Over</span>
  : row.ahead ? <span className="pill pill-info">Ahead of the days</span> : "";

/**
 * A budget against the books: each line's amount, the share of it the
 * days so far account for, what posted, what is left. What posted that
 * no line covers is listed too, as unbudgeted, so nothing hides in a
 * total.
 */
export default function BudgetReport() {
  return (
    <ReportView<Report, Row>
      title="Budget against the books"
      endpoint={(values) => (values.budget ? `/api/accounting/budgets/${values.budget}/report/` : null)}
      params={PARAMS}
      send={(values) => ({ as_of: values.as_of })}
      rows={(data) => data.rows.map((row, index) => ({ ...row, id: `${String(row.account)}-${String(row.centre ?? "")}-${index}` }))}
      above={(data) => (
        <p className="muted">
          {data.code} · {data.start} to {data.end}, {data.elapsed} of {data.days} days to {data.as_of}.{" "}
          Expenses: budget {money(data.totals.expense.amount)}, to date {money(data.totals.expense.to_date)}, posted {money(data.totals.expense.actual)}
          {data.totals.expense.unbudgeted !== "0.00" && `, unbudgeted ${money(data.totals.expense.unbudgeted)}`}.{" "}
          Income: budget {money(data.totals.income.amount)}, to date {money(data.totals.income.to_date)}, posted {money(data.totals.income.actual)}.
        </p>
      )}
      columns={[
        { key: "account_code", label: "Account", width: "6rem" },
        { key: "account_name", label: "" },
        { key: "centre_name", label: "Centre", render: (row) => String(row.centre_code || row.centre_name || "") },
        { key: "amount", label: "Budget", kind: "money", width: "10rem" },
        { key: "to_date", label: "To date", kind: "money", width: "10rem" },
        { key: "actual", label: "Posted", kind: "money", width: "10rem" },
        { key: "remaining", label: "Left", kind: "money", width: "10rem" },
        { key: "used_percent", label: "Used", kind: "quantity", width: "6rem", render: (row) => (row.used_percent == null ? "" : `${String(row.used_percent)}%`) },
        { key: "state", label: "", width: "10rem", render: state },
      ]}
      waiting="Choose a budget."
      empty="The budget has no lines and nothing posted in its span."
    />
  );
}
