import { ListView, type Column } from "../../views/ListView";

interface Machine { id: number; code: string; name: string; work_centre_name: string; capacity_per_hour: string | null;
  hours_per_day: string | null; is_active: boolean; [key: string]: unknown }

const columns: Column<Machine>[] = [
  { key: "code", label: "Machine", sort: "code", width: "9rem" },
  { key: "name", label: "Name" },
  { key: "work_centre_name", label: "Bank" },
  { key: "capacity_per_hour", label: "An hour", kind: "quantity", width: "8rem" },
  { key: "hours_per_day", label: "Hours a day", kind: "quantity", width: "8rem" },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function Machines() {
  return (
    <ListView<Machine>
      title="Machines"
      noun={["machine", "machines"]}
      endpoint="/api/manufacturing/machines/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/machines/${row.id}`}
      searchHint="Code or name"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/making/machines/new", permission: "manufacturing.add_machine" }}
    />
  );
}
