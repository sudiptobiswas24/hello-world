import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
];

export default function TaxGroups() {
  return (
    <ListView<Row>
      title="Tax groups"
      noun={["tax group", "tax groups"]}
      endpoint="/api/accounting/tax-groups/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/tax-groups/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/tax-groups/new", permission: "accounting.add_taxgroup" }}
    />
  );
}
