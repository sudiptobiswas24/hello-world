import { date, quantity } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Issue { id: number; number: string; issue_date: string; work_order_number: string; makes: string; direction: string;
  warehouse_name: string; lines: unknown[]; posted: boolean; voided_at: string | null; posted_value: string | null; [key: string]: unknown }

const columns: Column<Issue>[] = [
  { key: "number", label: "Issue", sort: "number", width: "9rem" },
  { key: "issue_date", label: "Date", sort: "issue_date", width: "8rem", render: (row) => date(row.issue_date) },
  { key: "work_order_number", label: "Run", width: "9rem" },
  { key: "makes", label: "Making" },
  { key: "direction", label: "", width: "7rem", render: (row) => (row.direction === "return" ? "Return" : "Issue") },
  { key: "lines", label: "Lines", width: "5rem", render: (row) => quantity(String(row.lines.length)) },
  { key: "posted_value", label: "Value", kind: "money", width: "9rem" },
  { key: "posted", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : row.posted ? "Posted" : "Draft") },
];

export default function Issues() {
  return (
    <ListView<Issue>
      title="Material issues"
      noun={["material issue", "material issues"]}
      endpoint="/api/manufacturing/material-issues/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/issues/${row.id}`}
      searchHint="Issue, run or item"
      facets={[{ label: "Drafts", params: { posted: "false" } }, { label: "Returns", params: { direction: "return" } }]}
      create={{ href: "/production/issues/new", permission: "manufacturing.add_materialissue" }}
    />
  );
}
