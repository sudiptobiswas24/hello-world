import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "rate", label: "Rate", kind: "quantity" },
  { key: "scope", label: "Scope" },
  { key: "gst_head", label: "GST head" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function Taxes() {
  return (
    <ListView<Row>
      title="Taxes"
      noun={["tax", "taxes"]}
      endpoint="/api/accounting/taxes/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/taxes/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/taxes/new", permission: "accounting.add_tax" }}
    />
  );
}
