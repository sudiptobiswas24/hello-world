import { ListView, type Column } from "../../views/ListView";
import { ChangeList, type Change } from "../../views/HistoryPanel";
import { dateTime } from "../../lib/format";

type Row = Record<string, unknown> & { id: number; changes?: Change[] };

const COLUMNS: Column<Row>[] = [
  { key: "at", label: "When", sort: "at", width: "9rem", render: (row) => dateTime(String(row.at)) },
  { key: "who_name", label: "Who", width: "9rem", render: (row) => String(row.who_name || "nobody signed in") },
  { key: "through", label: "Through", width: "9rem" },
  { key: "record", label: "Record", width: "14rem" },
  { key: "label", label: "What", width: "7rem" },
  { key: "changes", label: "Old and new", render: (row) => <ChangeList changes={row.changes} /> },
];

/**
 * The edit log: every change to every record, with each field's old and
 * new value, who made it and through what (the screens, the admin, the
 * shell, a command). Narrowed by date, by kind of record or person
 * (search). Read only; nothing in it is ever removed.
 */
export default function EditLog() {
  return (
    <ListView<Row>
      title="Edit log"
      endpoint="/api/core/edit-log/"
      columns={COLUMNS}
      searchHint="Kind of record, person, field or through"
      noun={["change", "changes"]}
      facets={[{ label: "Made", params: { kind: "created" } }, { label: "Changed", params: { kind: "updated" } },
        { label: "Deleted", params: { kind: "deleted" } }, { label: "Actions", params: { kind: "action" } }]}
    />
  );
}
