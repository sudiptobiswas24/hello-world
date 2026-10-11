import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Count { id: number; number: string; count_date: string; warehouse_name: string; reason_name: string; posted: boolean; lines_count: number; [key: string]: unknown }

const columns: Column<Count>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "count_date", label: "Counted", width: "8rem", sort: "count_date", render: (row) => date(row.count_date) },
  { key: "warehouse_name", label: "Warehouse" },
  { key: "reason_name", label: "Reason" },
  { key: "lines_count", label: "Lines", width: "6rem", kind: "quantity", render: (row) => String(row.lines_count) },
  { key: "posted", label: "State", width: "7rem", render: (row) => (row.posted ? "Posted" : "Counting") },
];

/** Physical counts: what the shelf held against what the books said, and the difference booked. */
export default function Counts() {
  return (
    <ListView<Count>
      title="Stock counts"
      noun={["count", "counts"]}
      endpoint="/api/inventory/stock-counts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/counts/${row.id}`}
      searchHint="Number, memo"
      facets={[{ label: "Counting", params: { posted: "false" } }, { label: "Posted", params: { posted: "true" } }]}
      create={{ href: "/stores/counts/new", permission: "inventory.add_stockcount" }}
    />
  );
}
