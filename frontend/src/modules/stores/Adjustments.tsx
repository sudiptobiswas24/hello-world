import { date, money } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Adjustment { id: number; number: string; adjustment_date: string; warehouse_name: string; reason_name: string; total_value: string; posted: boolean; voided: boolean; [key: string]: unknown }

const columns: Column<Adjustment>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "adjustment_date", label: "Date", width: "8rem", sort: "adjustment_date", render: (row) => date(row.adjustment_date) },
  { key: "warehouse_name", label: "Warehouse" },
  { key: "reason_name", label: "Reason" },
  { key: "total_value", label: "Value", kind: "money", width: "10rem", render: (row) => money(row.total_value) },
  { key: "state", label: "State", width: "7rem", render: (row) => (row.voided ? "Voided" : row.posted ? "Posted" : "Draft") },
];

/** Stock written on or off for a reason: damage, samples, spares to a job, opening stock. */
export default function Adjustments() {
  return (
    <ListView<Adjustment>
      title="Adjustments"
      noun={["adjustment", "adjustments"]}
      endpoint="/api/inventory/stock-adjustments/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/adjustments/${row.id}`}
      searchHint="Number, memo"
      facets={[{ label: "Draft", params: { posted: "false" } }]}
      create={{ href: "/stores/adjustments/new", permission: "inventory.add_stockadjustment" }}
    />
  );
}
