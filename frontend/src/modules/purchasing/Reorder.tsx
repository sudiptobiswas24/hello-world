import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };


/** Items under their reorder level once what is on order and committed is counted, and how much to order to reach the target. */
export default function Reorder() {
  return (
    <ReportView<Row[], Row>
      title="What to reorder"
      endpoint="/api/purchasing/purchasing-reports/reorder/"
      params={[]}
      rows={(data) => data.map((row, index) => ({ ...row, id: index }))}
      columns={[
        { key: "item", label: "Item" },
        { key: "warehouse", label: "At", width: "10rem" },
        { key: "on_hand", label: "On hand", kind: "quantity", width: "9rem" },
        { key: "shortfall", label: "Short by", kind: "quantity", width: "9rem" },
        { key: "suggested", label: "Order", kind: "quantity", width: "9rem" },
      ]}
      empty="Nothing is under its reorder level."
    />
  );
}
