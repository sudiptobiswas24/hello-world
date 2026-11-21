import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "kind_label", label: "On", sort: "kind", width: "14rem" },
  { key: "label", label: "Field", sort: "label" },
  { key: "key", label: "Key", width: "10rem" },
  { key: "field_type", label: "Holds", kind: "status", width: "8rem" },
  { key: "required", label: "", width: "6rem", render: (row) => (row.required ? "Required" : "") },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Off") },
];

/** The columns the office asked for: each on one kind of record, shown on its screen. */
export default function CustomFields() {
  return (
    <ListView<Row>
      title="Custom fields"
      endpoint="/api/core/custom-fields/"
      columns={COLUMNS}
      rowHref={(row) => `/settings/custom-fields/${row.id}`}
      searchHint="Field or key"
      noun={["custom field", "custom fields"]}
      create={{ href: "/settings/custom-fields/new", permission: "core.add_customfield" }}
      facets={[{ label: "In use", params: { is_active: "true" } }, { label: "Off", params: { is_active: "false" } }]}
    />
  );
}
