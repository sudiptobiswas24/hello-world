import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Booking { id: number; number: string; booking_date: string; work_order_number: string; operation_name: string;
  shift_name: string; machine_code: string; minutes: string; quantity_completed: string | null; posted: boolean;
  voided_at: string | null; [key: string]: unknown }

const columns: Column<Booking>[] = [
  { key: "number", label: "Booking", sort: "number", width: "9rem" },
  { key: "booking_date", label: "Date", sort: "booking_date", width: "8rem", render: (row) => date(row.booking_date) },
  { key: "work_order_number", label: "Run", width: "9rem" },
  { key: "operation_name", label: "Step" },
  { key: "shift_name", label: "Shift", width: "8rem" },
  { key: "machine_code", label: "Machine", width: "8rem" },
  { key: "minutes", label: "Minutes", kind: "quantity", width: "7rem" },
  { key: "quantity_completed", label: "Done", kind: "quantity", width: "7rem" },
  { key: "posted", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : row.posted ? "Posted" : "Draft") },
];

export default function Bookings() {
  return (
    <ListView<Booking>
      title="Time booked"
      noun={["time booking", "time bookings"]}
      endpoint="/api/manufacturing/time-bookings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/time/${row.id}`}
      searchHint="Booking or run"
      facets={[{ label: "Drafts", params: { posted: "false" } }]}
      create={{ href: "/production/time/new", permission: "manufacturing.add_timebooking" }}
    />
  );
}
