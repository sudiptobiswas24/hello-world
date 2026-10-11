import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Section" },
  { key: "name", label: "Name" },
  { key: "rate_percent", label: "Rate %" },
  { key: "no_pan_rate_percent", label: "Without PAN %" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function TdsSections() {
  return (
    <ListView<Row>
      title="TDS sections"
      noun={["section", "sections"]}
      endpoint="/api/accounting/tds-sections/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/tds-sections/${row.id}`}
      searchHint="Section or name"
      create={{ href: "/settings/tds-sections/new", permission: "accounting.add_tdssection" }}
    />
  );
}
