import { RecordScreen } from "../../views/RecordScreen";
import { UOM, WORK_CENTRE } from "./refs";

type Row = Record<string, unknown> & { id: number };

/** The steps a recipe is made by, in order, and at which bank each runs or which contractor. */
export default function RoutingForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/routings/"
      back="/making/routings"
      backLabel="Routings"
      newTitle="New routing"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      state={(row) => (row.is_active ? null : { label: "Inactive", tone: "draft" })}
      permissions={{ add: "manufacturing.add_routing", change: "manufacturing.change_routing", delete: "manufacturing.delete_routing" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "description", label: "Description", kind: "textarea" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Steps", permission: "manufacturing.view_routingoperation", endpoint: "", query: () => ({}),
        rows: (record) => [...((record.operations as Row[]) ?? [])].sort((a, b) => Number(a.sequence) - Number(b.sequence)),
        columns: [
          { key: "sequence", label: "#", width: "4rem" },
          { key: "name", label: "Step" },
          { key: "work_centre_name", label: "Where", render: (row) => (row.is_outside ? "Outside" : String(row.work_centre_name || "")) },
          { key: "setup_minutes", label: "Setup min", kind: "quantity", width: "8rem" },
          { key: "units_per_hour", label: "Per hour", kind: "quantity", width: "8rem" },
        ],
        adder: { label: "Add a step", permission: "manufacturing.add_routingoperation",
          url: () => "/api/manufacturing/routing-operations/",
          fields: [
            { key: "sequence", label: "#", kind: "integer" },
            { key: "name", label: "Step" },
            { key: "work_centre", label: "Work centre", kind: "ref", ref: WORK_CENTRE },
            { key: "is_outside", label: "Done outside", kind: "bool" },
            { key: "outside_lead_days", label: "Days away", kind: "integer", initial: 0 },
            { key: "outside_cost_per_unit", label: "Outside cost a unit", kind: "decimal", places: 6 },
            { key: "setup_minutes", label: "Setup minutes", kind: "decimal", initial: "0" },
            { key: "units_per_hour", label: "Units an hour", kind: "decimal", hint: "With the unit it counts in, below" },
            { key: "rate_uom", label: "Rate unit", kind: "ref", ref: UOM },
          ],
          body: (values, record) => ({ routing: record.id, ...values }) },
        remover: { permission: "manufacturing.delete_routingoperation", url: (row) => `/api/manufacturing/routing-operations/${row.id}/` },
      }]}
    />
  );
}
