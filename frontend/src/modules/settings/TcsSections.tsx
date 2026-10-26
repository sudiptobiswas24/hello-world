import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code", width: "8rem" },
  { key: "name", label: "Collected on" },
  { key: "section_2025", label: "2025 Act" },
  { key: "form_code", label: "Return code", width: "7rem" },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active === false ? "Inactive" : "") },
];

/** The sections tax is collected at source under: scrap, for this plant. Their rates are kept by the day. */
export default function TcsSections() {
  return (
    <ListView<Row>
      title="TCS sections"
      noun={["section", "sections"]}
      endpoint="/api/accounting/tcs-sections/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/tcs-sections/${row.id}`}
      create={{ href: "/settings/tcs-sections/new", permission: "accounting.add_tcssection" }}
    />
  );
}
