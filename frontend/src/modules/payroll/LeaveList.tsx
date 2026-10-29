import { useAccess } from "../../auth/me";
import { ListView, type Column } from "../../views/ListView";

interface Leave {
  id: number;
  employee_name: string;
  leave_type: string;
  policy_name: string;
  start_date: string;
  end_date: string;
  half_day: boolean;
  days_taken: string | null;
  status: string;
  decided_by_name: string;
}

const columns: Column<Leave>[] = [
  { key: "employee_name", label: "Who" },
  { key: "policy_name", label: "Allowance", render: (row) => row.policy_name || row.leave_type },
  { key: "start_date", label: "From", kind: "date", sort: "start_date", width: "8rem" },
  { key: "end_date", label: "To", kind: "date", sort: "end_date", width: "8rem" },
  { key: "days_taken", label: "Days", kind: "quantity", width: "6rem" },
  { key: "decided_by_name", label: "Decided by", width: "10rem" },
  { key: "status", label: "State", kind: "status", sort: "status", width: "7rem" },
];

/**
 * Leave: one's own, and one's reports' for a manager; everyone's for HR.
 * Which rows come back is the server's decision, not this screen's.
 */
export default function LeaveList() {
  const { me } = useAccess();
  return (
    <ListView<Leave>
      title="Leave"
      noun={["leave request", "leave requests"]}
      endpoint="/api/hr/leave-requests/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/leave/${row.id}`}
      create={me.employee || me.is_superuser ? { href: "/payroll/leave/new", permission: "hr.add_leaverequest" } : undefined}
      facets={[
        { label: "Waiting", params: { status: "pending" } },
        { label: "Approved", params: { status: "approved" } },
        ...(me.employee ? [{ label: "Mine", params: { employee: String(me.employee) } }] : []),
      ]}
      searchHint="Name, number or reason"
    />
  );
}
