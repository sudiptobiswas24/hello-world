import { RecordScreen } from "../../views/RecordScreen";
import { MACHINE, SHIFT, STEP, VOID_FIELDS, draft, postedState } from "./refs";

/**
 * Minutes a step of a run took. Posted, they are charged to the run at
 * the bank's rate for an hour; the step says which run it belongs to.
 */
export default function BookingForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/time-bookings/"
      back="/production/time"
      backLabel="Time booked"
      newTitle="New time booking"
      heading={(row) => `${String(row.number || "Booking")} · ${String(row.work_order_number ?? "")}`}
      state={postedState}
      permissions={{ add: "manufacturing.add_timebooking", change: "manufacturing.change_timebooking",
        delete: "manufacturing.delete_timebooking" }}
      editable={draft}
      fields={[
        { key: "operation", label: "Step", kind: "pick", pick: STEP, createOnly: true,
          show: (row) => `${String(row.work_order_number)} · ${String(row.operation_name)}` },
        { key: "booking_date", label: "Date", kind: "date" },
        { key: "shift", label: "Shift", kind: "ref", ref: SHIFT },
        { key: "machine", label: "Machine", kind: "ref", ref: MACHINE, hint: "Empty: the step's own" },
        { key: "minutes", label: "Minutes", kind: "decimal" },
        { key: "quantity_completed", label: "Done in that time", kind: "decimal" },
        { key: "memo", label: "Memo", wide: true },
        { key: "hourly_rate", label: "Rate an hour", readOnly: true },
        { key: "posted_value", label: "Value", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "manufacturing.change_timebooking", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "manufacturing.change_timebooking", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided", fields: VOID_FIELDS },
      ]}
    />
  );
}
