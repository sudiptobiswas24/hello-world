import { date, quantity } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Forecast { id: number; item_label: string; warehouse_name: string; starts_on: string; ends_on: string; quantity: string; consumed: string; unconsumed: string; [key: string]: unknown }

const columns: Column<Forecast>[] = [
  { key: "starts_on", label: "From", width: "8rem", sort: "starts_on", render: (row) => date(row.starts_on) },
  { key: "ends_on", label: "To", width: "8rem", render: (row) => date(row.ends_on) },
  { key: "item_label", label: "Item" },
  { key: "warehouse_name", label: "Warehouse", width: "10rem" },
  { key: "quantity", label: "Expected", kind: "quantity", width: "9rem", render: (row) => quantity(row.quantity) },
  { key: "consumed", label: "Ordered against it", kind: "quantity", width: "10rem", render: (row) => quantity(row.consumed) },
  { key: "unconsumed", label: "Still expected", kind: "quantity", width: "9rem", render: (row) => quantity(row.unconsumed) },
];

/** What is expected to sell, period by period, used up by the orders that arrive against it. */
export default function Forecasts() {
  return (
    <ListView<Forecast>
      title="Forecasts"
      noun={["forecast", "forecasts"]}
      endpoint="/api/planning/forecasts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/forecasts/${row.id}`}
      searchHint="Item"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/production/forecasts/new", permission: "planning.add_forecast" }}
    />
  );
}
