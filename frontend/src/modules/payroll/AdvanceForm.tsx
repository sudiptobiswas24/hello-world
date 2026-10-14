import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const EMPLOYEE: FieldDef["pick"] = { endpoint: "/api/hr/employees/", permission: "hr.view_employee", label: (row: Row) => `${String(row.employee_number)} · ${String(row.name)}` };
const PAYMENT: FieldDef["pick"] = { endpoint: "/api/accounting/payments/", permission: "accounting.view_payment", label: (row: Row) => `${String(row.number)} · ${String(row.amount)}` };
const COMPONENT: FieldDef["pick"] = { endpoint: "/api/hr/pay-components/", permission: "hr.view_paycomponent", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** An advance or loan: given by a payment out, recovered by the pay runs; the balance is worked out. */
export default function AdvanceForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/salary-advances/"
      back="/payroll/advances"
      backLabel="Advances and loans"
      newTitle="New advance or loan"
      heading={(row) => `Advance · ${String(row.amount ?? "")}`}
      permissions={{ add: "hr.add_salaryadvance", change: "hr.change_salaryadvance", delete: "hr.delete_salaryadvance" }}
      editable={(row) => !row.voided}
      state={(row) => (row.voided ? { label: "Voided", tone: "bad" } : { label: `Left ${String(row.balance ?? "")}`, tone: "info" })}
      actions={[
        { label: "Void", path: "void", permission: "hr.void_salaryadvance", danger: true, when: (row) => !row.voided,
          done: "Voided", fields: [{ key: "on_date", label: "Voided on", kind: "date" }] },
      ]}
      fields={[
        { key: "employee", label: "Employee", kind: "pick", pick: EMPLOYEE },
        { key: "kind", label: "Kind", kind: "choice", choices: [["advance", "Salary advance"], ["loan", "Loan"]] },
        { key: "payment", label: "Paid by", kind: "pick", pick: PAYMENT, hint: "The posted payment out to them" },
        { key: "component", label: "Recovered by", kind: "pick", pick: COMPONENT },
        { key: "amount", label: "Amount", kind: "money" },
        { key: "instalment", label: "Instalment", kind: "money" },
        { key: "recover_from", label: "Recover from", kind: "date" },
        { key: "note", label: "Note" },
      ]}
    />
  );
}
