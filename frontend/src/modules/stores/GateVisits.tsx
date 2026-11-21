import { dateTime } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Visit { id: number; number: string; vehicle_number: string; driver_name: string; purpose: string; in_at: string; out_at: string | null; status: string; [key: string]: unknown }

const PURPOSE: Record<string, string> = {
  inward_order: "In, against an order", inward_job_work: "In, job work", outward_delivery: "Out, delivery",
  gate_pass: "Gate pass", visitor: "Visitor",
};

const columns: Column<Visit>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "vehicle_number", label: "Vehicle", width: "9rem" },
  { key: "driver_name", label: "Driver" },
  { key: "purpose", label: "For", render: (row) => PURPOSE[row.purpose] ?? row.purpose },
  { key: "in_at", label: "In", width: "10rem", sort: "in_at", render: (row) => dateTime(row.in_at) },
  { key: "out_at", label: "Out", width: "10rem", render: (row) => (row.out_at ? dateTime(row.out_at) : "") },
  { key: "status", label: "State", width: "7rem", kind: "status" },
];

/** The gate register: every vehicle in and out, by the gate's own clock. */
export default function GateVisits() {
  return (
    <ListView<Visit>
      title="Gate register"
      noun={["visit", "visits"]}
      endpoint="/api/inventory/gate-visits/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/gate/${row.id}`}
      searchHint="Number, vehicle, driver, reference"
      facets={[{ label: "In now", params: { status: "in" } }, { label: "In too long", params: { open_too_long: "true" } }]}
      create={{ href: "/stores/gate/new", permission: "inventory.add_gatevisit" }}
    />
  );
}
