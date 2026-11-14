import { RecordScreen } from "../../views/RecordScreen";

const WAREHOUSE = { endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse",
  label: (row: Record<string, unknown>) => String(row.name || row.code) };

export default function RouteForm() {
  return (
    <RecordScreen
      endpoint="/api/planning/transfer-routes/"
      back="/production/routes"
      backLabel="Transfer routes"
      newTitle="New transfer route"
      heading={(row) => `${String(row.from_name)} to ${String(row.to_name)}`}
      permissions={{ add: "planning.add_transferroute", change: "planning.change_transferroute", delete: "planning.delete_transferroute" }}
      fields={[
        { key: "from_warehouse", label: "From", kind: "ref", ref: WAREHOUSE },
        { key: "to_warehouse", label: "To", kind: "ref", ref: WAREHOUSE },
        { key: "lead_days", label: "Days on the road", kind: "integer" },
        { key: "priority", label: "Preference", kind: "integer", initial: "0", hint: "Lower is tried first" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
    />
  );
}
