import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "due_on", label: "Due", kind: "date", sort: "due_on", width: "8rem" },
  { key: "kind", label: "", kind: "status", width: "7rem" },
  { key: "about", label: "About" },
  { key: "summary", label: "What" },
  { key: "owner_name", label: "Who", width: "10rem" },
  { key: "done_on", label: "Done", kind: "date", width: "8rem" },
];

/** Calls, visits and notes against leads, opportunities and customers, and the follow-ups still to do. */
export default function Activities() {
  return (
    <ListView<Row>
      title="Calls and visits"
      noun={["activity", "activities"]}
      endpoint="/api/sales/activities/"
      columns={columns}
      facets={[
        { label: "To do", params: { done_on__isnull: "true" } },
        { label: "Done", params: { done_on__isnull: "false" } },
      ]}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/activities/${row.id}`}
      searchHint="What was said"
      create={{ href: "/sales/activities/new", permission: "sales.add_activity" }}
    />
  );
}
