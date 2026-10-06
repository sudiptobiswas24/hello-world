import { RecordScreen } from "../../views/RecordScreen";
import { EMPLOYEE_PARTY, PLAN } from "./extraRefs";

/**
 * An employee who carries customers, and the plan their commission is
 * worked out on. The customers they carry are set on each customer.
 */
export default function SalesRepForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/sales-reps/"
      back="/sales/reps"
      backLabel="Sales reps"
      newTitle="New sales rep"
      heading={(row) => String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "sales.add_salesrep", change: "sales.change_salesrep", delete: "sales.delete_salesrep" }}
      fields={[
        { key: "party", label: "Employee", kind: "pick", pick: EMPLOYEE_PARTY, createOnly: true, show: (row) => String(row.name ?? "") },
        { key: "plan", label: "Commission plan", kind: "ref", ref: PLAN },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
