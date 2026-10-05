import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Meter { id: number; code: string; serves: string; multiplier: string; installed_on: string; retired_on: string | null; [key: string]: unknown }

const columns: Column<Meter>[] = [
  { key: "code", label: "Meter", sort: "code", width: "10rem" },
  { key: "serves", label: "On" },
  { key: "multiplier", label: "Multiplier", kind: "quantity", width: "8rem" },
  { key: "installed_on", label: "Installed", width: "8rem", render: (row) => date(row.installed_on) },
  { key: "retired_on", label: "Retired", width: "8rem", render: (row) => date(row.retired_on) },
];

export default function Meters() {
  return (
    <ListView<Meter>
      title="Energy meters"
      noun={["meter", "meters"]}
      endpoint="/api/manufacturing/energy-meters/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/plant/meters/${row.id}`}
      searchHint="Meter"
      create={{ href: "/plant/meters/new", permission: "manufacturing.add_energymeter" }}
    />
  );
}
