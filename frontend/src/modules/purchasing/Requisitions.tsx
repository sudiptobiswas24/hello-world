import { ListView, type Column } from "../../views/ListView";
import { date, money } from "../../lib/format";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem", render: (row) => String(row.number || "Draft") },
  { key: "requested_by_name", label: "Asked by" },
  { key: "request_date", label: "Asked on", kind: "date", width: "9rem" },
  { key: "needed_by", label: "Needed by", width: "9rem", render: (row) => (row.needed_by ? date(String(row.needed_by)) : "") },
  { key: "estimated_total", label: "About", width: "9rem", render: (row) => money(String(row.estimated_total ?? "0")) },
  { key: "status", label: "Status", width: "8rem" },
];

export default function Requisitions() {
  return (
    <ListView<Row>
      title="Requisitions"
      noun={["requisition", "requisitions"]}
      endpoint="/api/purchasing/requisitions/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/requisitions/${row.id}`}
      searchHint="Number, who asked or why"
      create={{ href: "/purchasing/requisitions/new", permission: "purchasing.add_purchaserequisition" }}
    />
  );
}
