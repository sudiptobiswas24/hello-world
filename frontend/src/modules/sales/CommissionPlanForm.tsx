import { RecordScreen } from "../../views/RecordScreen";

/** What a rep earns on what they sell, and on what it is counted. */
export default function CommissionPlanForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/commission-plans/"
      back="/sales/commission-plans"
      backLabel="Commission plans"
      newTitle="New commission plan"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "sales.add_commissionplan", change: "sales.change_commissionplan", delete: "sales.delete_commissionplan" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "percent", label: "%", kind: "decimal", places: 2, hint: "Commission rate, e.g. 2.50 for 2.5%" },
        { key: "basis", label: "Basis", kind: "choice", choices: [["invoiced", "What was invoiced"], ["paid", "What was collected"]], initial: "invoiced" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
