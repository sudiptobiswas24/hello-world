import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

export default function ForecastForm() {
  return (
    <RecordScreen
      endpoint="/api/planning/forecasts/"
      back="/production/forecasts"
      backLabel="Forecasts"
      newTitle="New forecast"
      heading={(row) => `${String(row.item_label)}, ${String(row.starts_on)}`}
      permissions={{ add: "planning.add_forecast", change: "planning.change_forecast", delete: "planning.delete_forecast" }}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: { endpoint: "/api/inventory/items/", permission: "inventory.view_item",
          label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` }, show: (row) => String(row.item_label) },
        { key: "warehouse", label: "Warehouse", kind: "ref", ref: { endpoint: "/api/inventory/warehouses/",
          permission: "inventory.view_warehouse", label: (row: Row) => String(row.name || row.code) } },
        { key: "starts_on", label: "From", kind: "date" },
        { key: "ends_on", label: "To", kind: "date" },
        { key: "quantity", label: "Expected", kind: "decimal" },
        { key: "is_active", label: "Planned against", kind: "bool", initial: true },
        { key: "notes", label: "Notes", kind: "textarea" },
        { key: "consumed", label: "Ordered against it", readOnly: true },
        { key: "unconsumed", label: "Still expected", readOnly: true },
      ]}
    />
  );
}
