import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "name", label: "Period", sort: "name" },
  { key: "start_date", label: "From", kind: "date", sort: "start_date", width: "8rem" },
  { key: "end_date", label: "To", kind: "date", sort: "end_date", width: "8rem" },
  { key: "closed", label: "State", width: "7rem",
    render: (row) => <span className={`pill pill-${row.closed ? "done" : "draft"}`}>{row.closed ? "Closed" : "Open"}</span> },
  { key: "closed_by_name", label: "Closed by", width: "9rem" },
  { key: "note", label: "Note" },
];

/** The months and years the books are reported for, and which are closed: nothing posts into a closed one. */
export default function Periods() {
  return (
    <ListView<Row>
      title="Accounting periods"
      endpoint="/api/accounting/periods/"
      columns={COLUMNS}
      rowHref={(row) => `/accounts/periods/${row.id}`}
      searchHint="Name or note"
      noun={["period", "periods"]}
      create={{ href: "/accounts/periods/new", permission: "accounting.add_accountingperiod" }}
      facets={[{ label: "Open", params: { closed: "false" } }, { label: "Closed", params: { closed: "true" } }]}
    />
  );
}
