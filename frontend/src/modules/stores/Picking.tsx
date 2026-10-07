import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { id: number; warehouse: string; bin: string; item: string; lot: string; quantity: string; for: string[]; problem: string; [key: string]: unknown }
interface List { date: string; warehouse: number | null; deliveries: string[]; rows: Row[] }

const PARAMS: ParamDef[] = [
  { key: "date", label: "Deliveries dated", kind: "date", initial: "today" },
  { key: "warehouse", label: "Warehouse", kind: "ref", ref: {
    endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse",
    label: (row) => `${String(row.code)} · ${String(row.name)}`, value: (row) => String(row.id),
  } },
];

/**
 * One route through the shelves for the day's draft shipments: bin by
 * bin in walking order, soonest batch first, two deliveries off one
 * shelf merged, and what the shelf cannot meet said on its row.
 */
export default function Picking() {
  const columns: Column<Row>[] = [
    { key: "warehouse", label: "Warehouse", width: "7rem" },
    { key: "bin", label: "Bin", width: "7rem", render: (row) => row.bin || "—" },
    { key: "item", label: "Goods" },
    { key: "lot", label: "Batch", width: "9rem", render: (row) => row.lot || "—" },
    { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
    { key: "for", label: "For", render: (row) => row.for.join(", ") },
    { key: "problem", label: "Problem" },
  ];
  return (
    <ReportView<List, Row>
      title="Pick list"
      endpoint="/api/sales/deliveries/pick-list/"
      params={PARAMS}
      rows={(data) => data.rows}
      columns={columns}
      empty="No draft delivery is dated that day."
      above={(data) => (data.deliveries.length === 0 ? null : (
        <p className="muted">
          For {data.deliveries.join(", ")}.{" "}
          <a className="btn" href={`/api/sales/deliveries/pick-list/pdf/?date=${data.date}${data.warehouse ? `&warehouse=${data.warehouse}` : ""}`} target="_blank" rel="noopener">Pick list PDF</a>
        </p>
      ))}
    />
  );
}
