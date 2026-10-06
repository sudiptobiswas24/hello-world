import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** One line of a payslip: an earning, a deduction or the employer's share, how it is worked out and where it posts. */
export default function PayComponentForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/pay-components/"
      back="/payroll/pay-components"
      backLabel="Pay components"
      newTitle="New pay component"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "hr.add_paycomponent", change: "hr.change_paycomponent", delete: "hr.delete_paycomponent" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "kind", label: "Kind", kind: "choice", choices: [["earning", "Earning"], ["deduction", "Employee deduction"], ["employer_cost", "Employer cost"]] },
        { key: "basis", label: "Basis", kind: "choice", choices: [["fixed", "Fixed amount"], ["percent", "Percentage of its base (taxable gross unless named)"], ["per_hour", "Rate per hour"], ["per_unit", "Rate per unit produced"], ["overtime", "Rate per overtime hour on the attendance register"], ["slab", "Amount from a slab of its base"]], initial: "fixed" },
        { key: "expense_account", label: "Expense account", kind: "pick", pick: ACCOUNT, hint: "Where an earning or an employer cost is charged" },
        { key: "liability_account", label: "Liability account", kind: "pick", pick: ACCOUNT, hint: "What a deduction or employer contribution is owed into — tax payable, pension payable" },
        { key: "measure", label: "Measure", hint: "For piece work, what is counted" },
        { key: "is_taxable", label: "Taxable", kind: "bool", initial: true, hint: "Counts towards the gross that percentage components are worked out from" },
        { key: "reduces_for_unpaid_leave", label: "Reduces for unpaid leave", kind: "bool", initial: false, hint: "Prorate this down for unpaid days in the period" },
        { key: "sequence", label: "Sequence", kind: "integer", initial: 100, hint: "Order on the payslip, and the order components are worked out in — a percentage can…" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "base_ceiling", label: "Base ceiling", kind: "decimal", places: 2, hint: "The base is capped here" },
        { key: "coverage_ceiling", label: "Coverage ceiling", kind: "decimal", places: 2, hint: "Applies only while what decides coverage is at or under this" },
        { key: "coverage_period_months", label: "Coverage period months", kind: "integer", initial: 1, hint: "Coverage is decided by the first slip in each block of this many months from April,…" },
        { key: "rounding", label: "Rounding", kind: "choice", choices: [["paisa", "To the paisa"], ["rupee", "To the nearest rupee"], ["rupee_up", "Up to the next rupee"]], initial: "paisa" },
        { key: "remit_by_day", label: "Remit by day", kind: "integer", hint: "Day of the following month what is owed must be paid over" },
      ]}
      panels={[{
        // For a slab component: the band its base falls in decides the
        // amount (professional tax by salary, by month where it differs).
        title: "Slabs", permission: "hr.view_paycomponentslab", endpoint: "/api/hr/pay-component-slabs/",
        query: (component) => ({ component: String(component.id) }),
        columns: [
          { key: "above", label: "Above", kind: "money", width: "10rem" },
          { key: "up_to", label: "Up to", kind: "money", width: "10rem" },
          { key: "month", label: "Month", width: "6rem", render: (row) => (row.month ? String(row.month) : "Any") },
          { key: "amount", label: "Amount", kind: "money", width: "10rem" },
        ],
        adder: { label: "Add a slab", permission: "hr.add_paycomponentslab", url: () => "/api/hr/pay-component-slabs/",
          when: (component) => component.basis === "slab",
          fields: [
            { key: "above", label: "Above", kind: "money" },
            { key: "up_to", label: "Up to", kind: "money", hint: "Empty: no top" },
            { key: "month", label: "Only in month", kind: "integer", hint: "1 to 12; empty for every month" },
            { key: "amount", label: "Amount", kind: "money" },
          ],
          body: (values, component) => ({ ...values, component: component.id }) },
        remover: { permission: "hr.delete_paycomponentslab", url: (row) => `/api/hr/pay-component-slabs/${String(row.id)}/` },
      }]}
    />
  );
}
