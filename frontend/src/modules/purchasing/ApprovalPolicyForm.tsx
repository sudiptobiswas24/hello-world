import { money } from "../../lib/format";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;
type Line = Row & { id: number };

const ROLE: FieldDef["ref"] = { endpoint: "/api/core/roles/", permission: "auth.view_group", label: (row: Row) => String(row.name) };

/** The amounts past which a purchase order needs a second pair of eyes, and whose eyes up to what. */
export default function ApprovalPolicyForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/approval-policies/"
      back="/purchasing/approval-policies"
      backLabel="Purchase approval"
      newTitle="New approval policy"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "purchasing.add_purchaseapprovalpolicy", change: "purchasing.change_purchaseapprovalpolicy", delete: "purchasing.delete_purchaseapprovalpolicy" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "max_order_value", label: "Orders over", kind: "money", hint: "Orders above this total need approval" },
        { key: "max_line_value", label: "Lines over", kind: "money", hint: "Catches one very large line inside an otherwise ordinary order" },
        { key: "require_approval_without_vendor_price", label: "Typed prices need approval", kind: "bool", initial: false, hint: "Need approval when a line's price was typed in rather than taken from an agreed vendor…" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Who signs, up to what", permission: "purchasing.view_approvaltier", endpoint: "", query: () => ({}),
        rows: (policy) => (policy.tiers as Line[]) ?? [],
        columns: [
          { key: "group_name", label: "Role" },
          { key: "up_to_amount", label: "Up to", kind: "money", width: "12rem", render: (row) => (row.up_to_amount == null ? "No ceiling" : money(String(row.up_to_amount))) },
        ],
        adder: { label: "Add a tier", permission: "purchasing.add_approvaltier", url: () => "/api/purchasing/approval-tiers/",
          fields: [
            { key: "group", label: "Role", kind: "ref", ref: ROLE },
            { key: "up_to_amount", label: "Up to", kind: "money", hint: "Leave empty for no ceiling: the last tier" },
          ],
          body: (values, policy) => ({ ...values, policy: policy.id }) },
        remover: { permission: "purchasing.delete_approvaltier", url: (row) => `/api/purchasing/approval-tiers/${row.id}/` },
      }]}
    />
  );
}
