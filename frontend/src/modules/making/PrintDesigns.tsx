import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "customer_name", label: "Customer" },
  { key: "colours", label: "Colours", kind: "quantity" },
  { key: "approved_on", label: "Approved" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function PrintDesigns() {
  return (
    <ListView<Row>
      title="Print designs"
      noun={["print design", "print designs"]}
      endpoint="/api/manufacturing/print-designs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/designs/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/designs/new", permission: "manufacturing.add_printdesign" }}
    />
  );
}
