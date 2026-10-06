import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "category", label: "Category" },
  { key: "conversion_factor", label: "Factor", kind: "quantity" },
];

export default function Units() {
  return (
    <ListView<Row>
      title="Units of measure"
      noun={["unit of measure", "units of measure"]}
      endpoint="/api/core/units-of-measure/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/units/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/units/new", permission: "core.add_unitofmeasure" }}
    />
  );
}
