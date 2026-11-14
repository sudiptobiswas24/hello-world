import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Documents" },
  { key: "name", label: "Name" },
  { key: "next_value", label: "Next number" },
];

export default function DocumentSequences() {
  return (
    <ListView<Row>
      title="Document numbering"
      noun={["sequence", "sequences"]}
      endpoint="/api/core/document-sequences/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/settings/numbering/${row.id}`}
      searchHint="Code, name or prefix"
      create={{ href: "/settings/numbering/new", permission: "core.add_documentsequence" }}
    />
  );
}
