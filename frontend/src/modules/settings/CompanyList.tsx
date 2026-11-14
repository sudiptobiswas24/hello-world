import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "name", label: "Name" },
  { key: "legal_name", label: "Legal name" },
  { key: "tax_id", label: "GSTIN" },
];

export default function CompanyList() {
  return (
    <ListView<Row>
      title="Company"
      noun={["company", "company"]}
      endpoint="/api/core/company/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/company/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/settings/company/new", permission: "core.add_company" }}
    />
  );
}
