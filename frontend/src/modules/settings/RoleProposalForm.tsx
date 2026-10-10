import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const STATE: Record<string, { label: string; tone: string }> = {
  pending: { label: "Waiting", tone: "open" },
  confirmed: { label: "Confirmed", tone: "done" },
  declined: { label: "Declined", tone: "draft" },
  lapsed: { label: "Lapsed", tone: "draft" },
};

/** One proposed role: confirmed by someone who holds it, declined, or withdrawn by whoever proposed it. */
export default function RoleProposalForm() {
  return (
    <RecordScreen
      endpoint="/api/core/role-proposals/"
      back="/settings/role-proposals"
      backLabel="Roles to confirm"
      newTitle="Proposed role"
      heading={(row) => `${String(row.role ?? "")} for ${String(row.username ?? "")}`}
      state={(row) => STATE[String(row.status)] ?? null}
      permissions={{}}
      fields={[
        { key: "username", label: "Login", readOnly: true },
        { key: "role", label: "Role", readOnly: true },
        { key: "proposed_by_name", label: "Proposed by", readOnly: true },
        { key: "proposed_at", label: "On", kind: "date", readOnly: true },
        { key: "decided_by_name", label: "Decided by", readOnly: true, existingOnly: true },
        { key: "decided_at", label: "Decided on", kind: "date", readOnly: true, existingOnly: true },
      ]}
      actions={[
        { label: "Confirm", path: "confirm", permission: "core.confirm_roleproposal", primary: true,
          when: (row: Row) => Boolean(row.may_confirm), done: "Confirmed: the role is given",
          // A password the proposer set stops working once the role is given (O157): the confirmer may issue the next.
          fields: [{ key: "password", label: "Their password",
            hint: "Optional. A password set by whoever proposed this stops working now; hand this one over yourself" }] },
        { label: "Decline", path: "decline", permission: "core.confirm_roleproposal", danger: true,
          when: (row: Row) => Boolean(row.may_decline), done: "Declined: the role is not given" },
      ]}
    />
  );
}
