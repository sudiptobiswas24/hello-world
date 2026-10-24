import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "max_order_value", label: "Orders over", kind: "money" },
  { key: "max_line_value", label: "Lines over", kind: "money" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function ApprovalPolicies() {
  return (
    <ListView<Row>
      title="Purchase approval"
      noun={["approval policy", "approval policies"]}
      endpoint="/api/purchasing/approval-policies/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/approval-policies/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/purchasing/approval-policies/new", permission: "purchasing.add_purchaseapprovalpolicy" }}
    />
  );
}
