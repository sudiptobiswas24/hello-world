import { money, quantity } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { item: string; item_name: string; warehouse_name: string; uom: string; quantity: string; unit_cost: string; value: string; method: string; [key: string]: unknown }
interface Data { as_of: string; total_value: string; rows: Row[] }

const PARAMS: ParamDef[] = [
  { key: "as_of", label: "As at", kind: "date", initial: "today" },
  { key: "warehouse", label: "Warehouse", kind: "ref",
    ref: { endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse",
      label: (row) => String(row.name || row.code), value: (row) => String(row.id) } },
];

/** What is on the shelves, where, at what cost: replayed from the stock ledger, never stored. */
export default function Valuation() {
  return (
    <ReportView<Data, Row>
      title="Stock value"
      endpoint="/api/inventory/stock-reports/valuation/"
      params={PARAMS}
      rows={(data) => data.rows}
      columns={[
        { key: "item", label: "Item", width: "10rem" },
        { key: "item_name", label: "Name" },
        { key: "warehouse_name", label: "Warehouse" },
        { key: "quantity", label: "Quantity", kind: "quantity", render: (row) => `${quantity(row.quantity)} ${row.uom}` },
        { key: "unit_cost", label: "Cost each", kind: "money", render: (row) => money(row.unit_cost, 4) },
        { key: "value", label: "Value", kind: "money" },
      ]}
      foot={(_rows, data) => ["Total", null, null, null, null, money(data.total_value)]}
      empty="Nothing on hand."
    />
  );
}
