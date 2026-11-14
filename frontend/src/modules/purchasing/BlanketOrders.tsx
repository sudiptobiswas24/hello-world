import { ListView, type Column } from "../../views/ListView";
import { money } from "../../lib/format";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem", render: (row) => String(row.number || "Draft") },
  { key: "vendor_name", label: "Vendor" },
  { key: "reference", label: "Their reference", width: "10rem" },
  { key: "start_date", label: "From", kind: "date", width: "9rem" },
  { key: "end_date", label: "To", kind: "date", width: "9rem" },
  { key: "total", label: "Agreed", width: "10rem", render: (row) => money(String(row.total ?? "0")) },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

export default function BlanketOrders() {
  return (
    <ListView<Row>
      title="Blanket orders"
      noun={["blanket order", "blanket orders"]}
      endpoint="/api/purchasing/blanket-orders/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/blanket-orders/${row.id}`}
      searchHint="Number, reference or vendor"
      create={{ href: "/purchasing/blanket-orders/new", permission: "purchasing.add_blanketorder" }}
    />
  );
}
