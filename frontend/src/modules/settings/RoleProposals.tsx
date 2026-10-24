import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "username", label: "Login", width: "10rem" },
  { key: "role", label: "Role" },
  { key: "proposed_by_name", label: "Proposed by", width: "12rem" },
  { key: "proposed_at", label: "On", kind: "date", sort: "proposed_at", width: "8rem" },
  { key: "status", label: "", kind: "status", width: "10rem" },
];

/**
 * Roles the keeper of logins proposed and could not give alone: each is
 * given when someone who holds it confirms it, never by whoever proposed
 * it, and nobody decides one for their own login.
 */
export default function RoleProposals() {
  return (
    <ListView<Row>
      title="Roles to confirm"
      endpoint="/api/core/role-proposals/"
      columns={COLUMNS}
      rowHref={(row) => `/settings/role-proposals/${row.id}`}
      searchHint="Login or role"
      noun={["proposed role", "proposed roles"]}
      facets={[
        { label: "Waiting for me", params: { waiting: "true" } },
        { label: "Waiting", params: { status: "pending" } },
        { label: "Confirmed", params: { status: "confirmed" } },
        { label: "Declined", params: { status: "declined" } },
        { label: "Lapsed", params: { status: "lapsed" } },
      ]}
    />
  );
}
