import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Reading { id: number; meter_code: string; shift_date: string; shift_name: string; reading: string; voided_at: string | null; [key: string]: unknown }

const columns: Column<Reading>[] = [
  { key: "shift_date", label: "Day", width: "8rem", sort: "shift_date", render: (row) => date(row.shift_date) },
  { key: "shift_name", label: "Shift", width: "8rem" },
  { key: "meter_code", label: "Meter" },
  { key: "reading", label: "Reading", kind: "quantity", width: "10rem" },
  { key: "voided_at", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : "") },
];

/** Readings as the meters were read: entered and voided, never edited. */
export default function Readings() {
  return (
    <ListView<Reading>
      title="Meter readings"
      noun={["reading", "readings"]}
      endpoint="/api/manufacturing/meter-readings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/plant/readings/${row.id}`}
      searchHint="Meter"
      facets={[{ label: "Standing", params: { voided_at__isnull: "true" } }]}
      create={{ href: "/plant/readings/new", permission: "manufacturing.add_meterreading" }}
    />
  );
}
