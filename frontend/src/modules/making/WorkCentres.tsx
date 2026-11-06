import { ListView, type Column } from "../../views/ListView";

interface Centre { id: number; code: string; name: string; capacity_per_hour: string | null; available_hours_per_day: string;
  conversion_rate_per_hour: string; efficiency_percent: string; is_active: boolean; [key: string]: unknown }

const columns: Column<Centre>[] = [
  { key: "code", label: "Bank", sort: "code", width: "9rem" },
  { key: "name", label: "Name" },
  { key: "capacity_per_hour", label: "An hour", kind: "quantity", width: "8rem" },
  { key: "available_hours_per_day", label: "Hours a day", kind: "quantity", width: "8rem" },
  { key: "efficiency_percent", label: "Efficiency %", kind: "quantity", width: "8rem" },
  { key: "conversion_rate_per_hour", label: "Cost an hour", kind: "money", width: "9rem" },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function WorkCentres() {
  return (
    <ListView<Centre>
      title="Work centres"
      noun={["work centre", "work centres"]}
      endpoint="/api/manufacturing/work-centres/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/work-centres/${row.id}`}
      searchHint="Code or name"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/making/work-centres/new", permission: "manufacturing.add_workcentre" }}
    />
  );
}
