import { RecordScreen } from "../../views/RecordScreen";

export default function StatutoryFigureForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/statutory-figures/"
      back="/payroll/statutory-figures"
      backLabel="Statutory figures"
      newTitle="New statutory figure"
      heading={(row) => `${String(row.kind ?? "")} from ${String(row.effective_from ?? "")}`}
      state={(row) => (row.confirmed_on ? { label: "Confirmed", tone: "done" } : { label: "To confirm", tone: "draft" })}
      permissions={{ add: "hr.add_statutoryfigure", change: "hr.change_statutoryfigure", delete: "hr.delete_statutoryfigure" }}
      fields={[
        { key: "kind", label: "Figure", kind: "choice", choices: [
          ["epf_ceiling", "EPF wage ceiling"], ["eps_ceiling", "EPS wage ceiling"], ["overtime_multiplier", "Overtime multiple"],
          ["esi_employee_rate", "ESI, employee's share %"], ["esi_employer_rate", "ESI, employer's share %"], ["esi_ceiling", "ESI coverage ceiling"],
          ["bonus_minimum_percent", "Bonus, least %"], ["bonus_maximum_percent", "Bonus, most %"], ["bonus_floor", "Bonus calculation floor"],
          ["bonus_eligibility", "Bonus eligibility ceiling"],
        ] },
        { key: "state", label: "State", hint: "Blank applies in every state" },
        { key: "effective_from", label: "Applies from", kind: "date" },
        { key: "value", label: "Value", kind: "decimal" },
        { key: "source", label: "Source", hint: "The notification, or who confirmed it" },
        { key: "confirmed_on", label: "Confirmed on", kind: "date", existingOnly: true, readOnly: true },
      ]}
      actions={[
        // The figures the Controller read, so a row changed since is refused, not confirmed unseen.
        { label: "Confirm", path: "confirm", body: (_values, record) => ({ kind: record.kind, state: record.state,
            effective_from: record.effective_from, value: record.value }), permission: "hr.confirm_statutoryfigure",
          when: (row) => !row.confirmed_on, done: "Confirmed: runs are paid at it from its day" },
      ]}
    />
  );
}
