import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { item: string; warehouse: string; quantity: string; value: string; allowed: boolean; [key: string]: unknown }
interface Data { rows: Row[]; unexpected: number }

const PARAMS: ParamDef[] = [{ key: "as_of", label: "As at", kind: "date", initial: "today" }];

/** Shelf positions holding less than nothing: a receipt not booked, or an issue booked twice. */
export default function Negative() {
  return (
    <ReportView<Data, Row>
      title="Negative stock"
      endpoint="/api/inventory/stock-reports/negative/"
      params={PARAMS}
      rows={(data) => data.rows}
      above={(data) => data.unexpected > 0
        ? <p className="form-error" role="note">{data.unexpected} of these should not be: find the document that caused each.</p>
        : null}
      columns={[
        { key: "item", label: "Item" },
        { key: "warehouse", label: "Warehouse", width: "10rem" },
        { key: "quantity", label: "Quantity", kind: "quantity" },
        { key: "value", label: "Value", kind: "money" },
        { key: "allowed", label: "Allowed", width: "7rem", render: (row) => (row.allowed ? "Yes" : "No") },
      ]}
      empty="Nothing is below zero."
    />
  );
}
