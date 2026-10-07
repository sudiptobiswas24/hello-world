import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const COLUMNS: Column<Row>[] = [
  { key: "username", label: "Login", sort: "username", width: "10rem" },
  { key: "name", label: "Name", render: (row) => `${String(row.first_name ?? "")} ${String(row.last_name ?? "")}`.trim() },
  { key: "roles", label: "Roles", render: (row) => (row.roles as string[]).join(", ") },
  { key: "employee_number", label: "Employee", width: "8rem" },
  { key: "last_login", label: "Last signed in", kind: "date", sort: "last_login", width: "9rem" },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Deactivated") },
];

/** Who may sign in, and as what: the roles decide every screen and button. */
export default function Logins() {
  return (
    <ListView<Row>
      title="Logins and roles"
      endpoint="/api/core/users/"
      columns={COLUMNS}
      rowHref={(row) => `/settings/logins/${row.id}`}
      searchHint="Login, name or email"
      noun={["login", "logins"]}
      create={{ href: "/settings/logins/new", permission: "auth.add_user" }}
      facets={[{ label: "Active", params: { is_active: "true" } }, { label: "Deactivated", params: { is_active: "false" } }]}
    />
  );
}
