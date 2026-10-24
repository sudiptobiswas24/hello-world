import { RecordScreen } from "../../views/RecordScreen";
import { ITEM, ORDER } from "./extraRefs";

/**
 * What the customer sends for a job-work order: planning buys none of it,
 * and the invoice bills the work, not the material. Settled once the
 * order is confirmed.
 */
export default function SuppliedItemForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/supplied-items/"
      back="/sales/supplied-items"
      backLabel="Material the customer sends"
      newTitle="New supplied item"
      heading={(row) => `${String(row.order_number ?? "")} · ${String(row.item_label ?? "")}`}
      permissions={{ add: "sales.add_supplieditem", delete: "sales.delete_supplieditem" }}
      fields={[
        { key: "order", label: "Job-work order", kind: "pick", pick: { ...ORDER!, query: { is_job_work: "true", status: "draft" } },
          createOnly: true, show: (row) => String(row.order_number ?? "") },
        { key: "item", label: "They send", kind: "pick", pick: ITEM, createOnly: true, show: (row) => String(row.item_label ?? "") },
      ]}
    />
  );
}
