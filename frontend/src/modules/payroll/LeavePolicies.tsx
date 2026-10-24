import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "leave_type", label: "Leave type" },
  { key: "annual_days", label: "Days a year", kind: "quantity" },
];

export default function LeavePolicies() {
  return (
    <ListView<Row>
      title="Leave policies"
      noun={["leave policy", "leave policies"]}
      endpoint="/api/hr/leave-policies/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/leave-policies/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/payroll/leave-policies/new", permission: "hr.add_leavepolicy" }}
    />
  );
}
