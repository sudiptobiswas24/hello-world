import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
];

export default function Countries() {
  return (
    <ListView<Row>
      title="Countries"
      noun={["country", "countries"]}
      endpoint="/api/core/countries/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/countries/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/countries/new", permission: "core.add_country" }}
    />
  );
}
