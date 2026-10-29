import { ListView, type Column } from "../../views/ListView";

interface WorkOrder {
  id: number;
  number: string;
  item_sku: string;
  item_name: string;
  quantity_ordered: string;
  work_centre_code: string;
  scheduled_start: string | null;
  scheduled_end: string | null;
  status: string;
}

const columns: Column<WorkOrder>[] = [
  { key: "number", label: "Run", sort: "number", width: "11rem", render: (row) => row.number || "Draft" },
  { key: "item_sku", label: "Item", width: "10rem" },
  { key: "item_name", label: "" },
  { key: "quantity_ordered", label: "Quantity", kind: "quantity", width: "8rem" },
  { key: "work_centre_code", label: "Where", width: "7rem" },
  { key: "scheduled_start", label: "Starts", kind: "date", sort: "scheduled_start", width: "8rem" },
  { key: "scheduled_end", label: "Due", kind: "date", width: "8rem" },
  { key: "status", label: "State", kind: "status", width: "7rem" },
];

export default function WorkOrders() {
  return (
    <ListView<WorkOrder>
      title="Work orders"
      noun={["run", "runs"]}
      endpoint="/api/manufacturing/work-orders/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/work-orders/${row.id}`}
      searchHint="Run number, item code or name"
      facets={[
        { label: "Drafts", params: { status: "draft" } },
        { label: "On the floor", params: { status: "released" } },
        { label: "Closed", params: { status: "closed" } },
      ]}
    />
  );
}
