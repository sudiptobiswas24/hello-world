import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function FiscalPositions() {
  return (
    <ListView<Row>
      title="Fiscal positions"
      noun={["fiscal position", "fiscal positions"]}
      endpoint="/api/accounting/fiscal-positions/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/fiscal-positions/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/fiscal-positions/new", permission: "accounting.add_fiscalposition" }}
    />
  );
}
