import { money, quantity } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { item: string; warehouse: string; quantity: string; value: string; [key: string]: unknown }
interface Data { since: string; total_value: string; rows: Row[] }

const PARAMS: ParamDef[] = [{ key: "since", label: "Not moved since", kind: "date", initial: "-90", required: true }];

/** Stock nothing has drawn on since a date: money on the shelf, and the next write-off. */
export default function SlowMoving() {
  return (
    <ReportView<Data, Row>
      title="Slow-moving stock"
      endpoint="/api/inventory/stock-reports/slow-moving/"
      params={PARAMS}
      rows={(data) => data.rows}
      columns={[
        { key: "item", label: "Item" },
        { key: "warehouse", label: "Warehouse", width: "10rem" },
        { key: "quantity", label: "Quantity", kind: "quantity", render: (row) => quantity(row.quantity) },
        { key: "value", label: "Value", kind: "money" },
      ]}
      foot={(_rows, data) => ["Total", null, null, money(data.total_value)]}
      empty="Everything has moved since then."
    />
  );
}
