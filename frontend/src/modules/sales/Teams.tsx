import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code", sort: "code", width: "8rem" },
  { key: "name", label: "Team", sort: "name" },
  { key: "leader_name", label: "Leader" },
  { key: "member_count", label: "Reps", kind: "quantity", width: "6rem" },
  { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

/** Reps under a leader, with a number to make between them. */
export default function Teams() {
  return (
    <ListView<Row>
      title="Sales teams"
      noun={["team", "teams"]}
      endpoint="/api/sales/sales-teams/"
      columns={columns}
      rowHref={(row) => `/sales/teams/${row.id}`}
      searchHint="Code or name"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/sales/teams/new", permission: "sales.add_salesteam" }}
    />
  );
}
