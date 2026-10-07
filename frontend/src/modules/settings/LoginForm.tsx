import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ROLE: FieldDef["ref"] = { endpoint: "/api/core/roles/", permission: "auth.view_group", label: (row: Row) => String(row.name) };

/**
 * One login: who it is, its roles, and whether it still signs in. A new
 * one is given its first password here; a leaver is deactivated, never
 * deleted, so what they signed stays theirs.
 */
export default function LoginForm() {
  return (
    <RecordScreen
      endpoint="/api/core/users/"
      trail="auth.user"
      back="/settings/logins"
      backLabel="Logins and roles"
      newTitle="New login"
      heading={(row) => `${String(row.username ?? "")}${row.first_name ? ` · ${String(row.first_name)} ${String(row.last_name ?? "")}` : ""}`}
      state={(row) => (row.is_active === false ? { label: "Deactivated", tone: "draft" } : { label: "Active", tone: "open" })}
      permissions={{ add: "auth.add_user", change: "auth.change_user" }}
      fields={[
        { key: "username", label: "Login", createOnly: true, hint: "Short, no spaces: asha" },
        { key: "first_name", label: "First name" },
        { key: "last_name", label: "Last name" },
        { key: "email", label: "Email", hint: "Where the morning's checks go" },
        { key: "password", label: "First password", newOnly: true, hint: "Hand it over once; they can change it at sign-in" },
        { key: "employee_number", label: "Employee", readOnly: true, existingOnly: true,
          show: (row) => (row.employee_number ? `${String(row.employee_number)} · ${String(row.employee_name ?? "")}` : "Not linked: set it on the employee") },
        { key: "last_login", label: "Last signed in", readOnly: true, existingOnly: true, kind: "date" },
      ]}
      actions={[
        { label: "Set a password", path: "set_password", permission: "auth.change_user", done: "Password set",
          fields: [{ key: "password", label: "New password" }] },
        { label: "Deactivate", path: "deactivate", permission: "auth.change_user", danger: true, when: (row) => row.is_active !== false,
          done: "Deactivated: the login no longer signs in" },
        { label: "Reactivate", path: "reactivate", permission: "auth.change_user", when: (row) => row.is_active === false, done: "Reactivated" },
      ]}
      panels={[
        {
          title: "Roles", permission: "auth.view_user", endpoint: "", query: () => ({}),
          rows: (record) => ((record.roles as string[]) ?? []).map((name) => ({ id: name, name })),
          columns: [{ key: "name", label: "Role" }],
          adder: {
            label: "Give a role", permission: "auth.change_user", url: (record) => `/api/core/users/${String(record.id)}/grant/`,
            fields: [{ key: "role", label: "Role", kind: "ref", ref: ROLE }],
            body: (values) => ({ role: values.role }),
          },
          rowActions: [
            { label: "Take away", permission: "auth.change_user", method: "POST", done: "Role taken away",
              url: (row, record) => `/api/core/users/${String(record.id)}/revoke/`, body: (row) => ({ role: row.name }) },
          ],
        },
      ]}
    />
  );
}
