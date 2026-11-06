import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };
interface Run { made: unknown[]; refused: { code: string; why: string }[] }

const COLUMNS: Column<Row>[] = [
  { key: "code", label: "Code", sort: "code", width: "8rem" },
  { key: "memo", label: "What" },
  { key: "interval", label: "Every", width: "9rem",
    render: (row) => `${Number(row.interval_count) > 1 ? `${String(row.interval_count)} × ` : ""}${String(row.interval)}` },
  { key: "next_run_date", label: "Next", kind: "date", sort: "next_run_date", width: "8rem" },
  { key: "auto_post", label: "", width: "6rem", render: (row) => (row.auto_post ? "Posts" : "Drafts") },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Stopped") },
];

/** The entries the books take on a schedule: rent accrued, insurance amortised. Each run is a journal entry. */
export default function RecurringJournals() {
  return (
    <ListView<Row>
      title="Recurring journals"
      noun={["schedule", "schedules"]}
      endpoint="/api/accounting/recurring-journals/"
      columns={COLUMNS}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/recurring-journals/${row.id}`}
      searchHint="Code or memo"
      facets={[{ label: "Running", params: { is_active: "true" } }]}
      create={{ href: "/accounts/recurring-journals/new", permission: "accounting.add_recurringjournal" }}
      actions={[
        { label: "Take every entry now due", permission: "accounting.add_journalentry", path: "/api/accounting/recurring-journals/run/",
          confirm: "Take the entries every running schedule has due today?",
          done: (result) => {
            const run = result as Run;
            return `${run.made.length} entries taken${run.refused.length ? `; ${run.refused.map((r) => `${r.code}: ${r.why}`).join(" ")}` : ""}`;
          } },
      ]}
    />
  );
}
