import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "planned_order", label: "Planned order", width: "9rem" },
  { key: "describes", label: "For" },
  { key: "source", label: "Source", kind: "status", width: "9rem" },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
  { key: "needed_by", label: "Needed by", kind: "date", width: "8rem" },
];

/** What each planned order is for: the order line, the run or the forecast whose need it covers. */
export default function PlannedDemands() {
  return (
    <ListView<Row>
      title="What planned orders are for"
      noun={["demand", "demands"]}
      endpoint="/api/planning/planned-demands/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Item"
    />
  );
}
