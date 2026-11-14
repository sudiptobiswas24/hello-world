import { ListView, type Column } from "../../views/ListView";

interface Entry {
  id: number;
  date: string;
  reference: string;
  memo: string;
  posted: boolean;
  reverses: number | null;
}

const columns: Column<Entry>[] = [
  { key: "date", label: "Date", kind: "date", sort: "date", width: "8rem" },
  { key: "reference", label: "Reference", width: "12rem", render: (row) => row.reference || `#${row.id}` },
  { key: "memo", label: "What" },
  {
    key: "state", label: "State", width: "8rem",
    render: (row) => row.reverses
      ? <span className="pill pill-info">Reversal</span>
      : <span className={`pill pill-${row.posted ? "done" : "draft"}`}>{row.posted ? "Posted" : "Draft"}</span>,
  },
];

export default function Journals() {
  return (
    <ListView<Entry>
      title="Journal entries"
      noun={["entry", "entries"]}
      endpoint="/api/accounting/journal-entries/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/journals/${row.id}`}
      create={{ href: "/accounts/journals/new", permission: "accounting.add_journalentry" }}
      searchHint="Reference or memo"
      facets={[{ label: "Drafts", params: { posted: "false" } }, { label: "Posted", params: { posted: "true" } }]}
    />
  );
}
