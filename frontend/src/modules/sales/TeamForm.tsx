import { RecordScreen } from "../../views/RecordScreen";
import { REP } from "./extraRefs";

/**
 * One team: its leader, and the reps whose sales count to its targets
 * (each rep names the team on their own page).
 */
export default function TeamForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/sales-teams/"
      back="/sales/teams"
      backLabel="Sales teams"
      newTitle="New sales team"
      heading={(row) => `${String(row.code ?? "")} · ${String(row.name ?? "")}`}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "sales.add_salesteam", change: "sales.change_salesteam", delete: "sales.delete_salesteam" }}
      fields={[
        { key: "code", label: "Code", createOnly: true },
        { key: "name", label: "Team" },
        { key: "leader", label: "Leader", kind: "ref", ref: REP },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[
        {
          title: "Reps", permission: "sales.view_salesrep", endpoint: "/api/sales/sales-reps/",
          query: (team) => ({ team: String(team.id) }),
          columns: [
            { key: "name", label: "Rep" },
            { key: "plan_name", label: "Commission plan" },
            { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Inactive") },
          ],
          href: (row) => `/sales/reps/${row.id}`,
        },
        {
          title: "Targets", permission: "sales.view_salestarget", endpoint: "/api/sales/sales-targets/",
          query: (team) => ({ team: String(team.id) }),
          columns: [
            { key: "period_start", label: "From", kind: "date", width: "9rem" },
            { key: "period_end", label: "To", kind: "date", width: "9rem" },
            { key: "amount", label: "Target", kind: "money", width: "10rem" },
            { key: "note", label: "Note" },
          ],
          href: (row) => `/sales/targets/${row.id}`,
        },
      ]}
    />
  );
}
