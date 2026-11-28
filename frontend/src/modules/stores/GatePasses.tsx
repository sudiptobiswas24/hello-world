import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Pass { id: number; number: string; kind: string; pass_date: string; party_name: string; expected_return_date: string | null; status: string; [key: string]: unknown }

const columns: Column<Pass>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "pass_date", label: "Date", width: "8rem", sort: "pass_date", render: (row) => date(row.pass_date) },
  { key: "kind", label: "Kind", width: "9rem", render: (row) => (row.kind === "returnable" ? "Returnable" : "Non-returnable") },
  { key: "party_name", label: "To" },
  { key: "expected_return_date", label: "Due back", width: "8rem", sort: "expected_return_date",
    render: (row) => (row.expected_return_date ? date(row.expected_return_date) : "") },
  { key: "status", label: "State", width: "8rem", kind: "status" },
];

/** What leaves the gate on a pass: returnable, for repair, or not. */
export default function GatePasses() {
  return (
    <ListView<Pass>
      title="Gate passes"
      noun={["pass", "passes"]}
      endpoint="/api/inventory/gate-passes/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/gate-passes/${row.id}`}
      searchHint="Number, to whom, purpose"
      facets={[{ label: "Overdue", params: { overdue: "true" } }, { label: "Out", params: { status: "out" } },
        { label: "To approve", params: { status: "draft" } }]}
      create={{ href: "/stores/gate-passes/new", permission: "inventory.add_gatepass" }}
    />
  );
}
