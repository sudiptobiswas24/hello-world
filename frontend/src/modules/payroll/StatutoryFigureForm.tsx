import { RecordScreen } from "../../views/RecordScreen";

export default function StatutoryFigureForm() {
  return (
    <RecordScreen
      endpoint="/api/hr/statutory-figures/"
      back="/payroll/statutory-figures"
      backLabel="Statutory figures"
      newTitle="New statutory figure"
      heading={(row) => `${String(row.kind ?? "")} from ${String(row.effective_from ?? "")}`}
      permissions={{ add: "hr.add_statutoryfigure", change: "hr.change_statutoryfigure", delete: "hr.delete_statutoryfigure" }}
      fields={[
        { key: "kind", label: "Figure", kind: "choice", choices: [
          ["epf_ceiling", "EPF wage ceiling"], ["eps_ceiling", "EPS wage ceiling"], ["overtime_multiplier", "Overtime multiple"],
        ] },
        { key: "state", label: "State", hint: "Blank applies in every state" },
        { key: "effective_from", label: "Applies from", kind: "date" },
        { key: "value", label: "Value", kind: "decimal" },
        { key: "source", label: "Source", hint: "The notification, or who confirmed it" },
      ]}
    />
  );
}
