import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "code", label: "Code", sort: "code", width: "10rem" },
  { key: "name", label: "Name", sort: "name" },
  { key: "is_active", label: "Active", width: "6rem", render: (row) => (row.is_active ? "Yes" : "No") },
  { key: "note", label: "Note" },
];

/** The centres a cost is read under: the loom shed, the printing line, the office. */
export default function CostCentres() {
  return (
    <ListView<Row>
      title="Cost centres"
      endpoint="/api/accounting/cost-centres/"
      columns={COLUMNS}
      rowHref={(row) => `/accounts/cost-centres/${row.id}`}
      searchHint="Code or name"
      noun={["cost centre", "cost centres"]}
      create={{ href: "/accounts/cost-centres/new", permission: "accounting.add_costcentre" }}
      facets={[{ label: "Active", params: { is_active: "true" } }]}
    />
  );
}
