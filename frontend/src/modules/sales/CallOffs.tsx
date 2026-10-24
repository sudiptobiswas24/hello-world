import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "due_on", label: "Due", kind: "date", width: "8rem" },
  { key: "line_label", label: "Order line" },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
  { key: "reference", label: "Their reference", width: "11rem" },
];

export default function CallOffs() {
  return (
    <ListView<Row>
      title="Call-offs"
      noun={["call-off", "call-offs"]}
      endpoint="/api/sales/call-offs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/call-offs/${row.id}`}
      searchHint="Order, customer, item or reference"
      create={{ href: "/sales/call-offs/new", permission: "sales.add_calloff" }}
    />
  );
}
