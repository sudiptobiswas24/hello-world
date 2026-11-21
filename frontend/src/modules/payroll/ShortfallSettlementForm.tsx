import { money } from "../../lib/format";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const SLIP: FieldDef["pick"] = { endpoint: "/api/hr/payslips/", permission: "hr.view_payslip", label: (row: Row) => `Slip ${String(row.id)} · ${String(row.employee ?? "")}` };
const PAYMENT: FieldDef["pick"] = { endpoint: "/api/accounting/payments/", permission: "accounting.view_payment", label: (row: Row) => `${String(row.number)} · ${money(row.amount as string)}` };

/**
 * A former employee's shortfall settled: paid back by a posted payment in, or written off by the
 * Controller with a reason. Posted as made, so it is voided, never changed; what is still owed is worked out.
 */
export default function ShortfallSettlementForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/shortfall-settlements/"
      back="/payroll/owed-back"
      backLabel="Owed back by former employees"
      newTitle="Paid back or written off"
      heading={(row) => `${row.kind === "write_off" ? "Written off" : "Paid back"} · ${String(row.amount ?? "")}`}
      permissions={{ add: "hr.add_shortfallsettlement", change: "hr.change_shortfallsettlement", delete: "hr.delete_shortfallsettlement" }}
      editable={(row) => row.id === undefined}
      state={(row) => (row.stands === false ? { label: "Voided", tone: "bad" } : { label: `Still owed ${money(row.owed_back as string)}`, tone: "info" })}
      actions={[
        { label: "Void", path: "void", permission: "hr.void_shortfallsettlement", danger: true, when: (row) => row.stands === true,
          done: "Voided", fields: [{ key: "on_date", label: "Voided on", kind: "date" }] },
      ]}
      fields={[
        { key: "payslip", label: "Final slip", kind: "pick", pick: SLIP },
        { key: "kind", label: "Settled by", kind: "choice", choices: [["receipt", "Paid back"], ["write_off", "Written off"]] },
        { key: "amount", label: "Amount", kind: "money" },
        { key: "payment", label: "Paid back by", kind: "pick", pick: PAYMENT, hint: "Paid back: the posted payment in from them" },
        { key: "date", label: "Written off on", kind: "date", hint: "Written off: today if left empty" },
        { key: "reason", label: "Why written off" },
      ]}
    />
  );
}
