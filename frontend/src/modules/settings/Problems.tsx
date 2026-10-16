import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "ref", label: "Reference", sort: "ref", width: "7rem" },
  { key: "happened_at", label: "When", kind: "date", sort: "happened_at", width: "9rem" },
  { key: "user_name", label: "Who", width: "10rem" },
  { key: "kind", label: "What", width: "12rem" },
  { key: "message", label: "", render: (row) => String(row.message ?? "").slice(0, 120) },
  { key: "path", label: "Where", width: "16rem" },
  { key: "resolved_at", label: "", width: "7rem", render: (row) => (row.resolved_at ? "Dealt with" : "") },
];

/**
 * Every failure anyone hit, under the reference they were shown. Open
 * one for the traceback, and say what was done about it.
 */
export default function Problems() {
  return (
    <ListView<Row>
      title="Problems people hit"
      endpoint="/api/core/errors/"
      columns={COLUMNS}
      rowHref={(row) => `/settings/problems/${row.id}`}
      searchHint="Reference, page or message"
      noun={["problem", "problems"]}
      facets={[{ label: "Open", params: { resolved_at__isnull: "true" } }, { label: "Dealt with", params: { resolved_at__isnull: "false" } }]}
    />
  );
}
