import { ListView, type Column } from "../../views/ListView";

interface Shift { id: number; code: string; name: string; starts_at: string; ends_at: string; hours: string;
  crosses_midnight: boolean; is_active: boolean; [key: string]: unknown }

const columns: Column<Shift>[] = [
  { key: "code", label: "Shift", width: "8rem" },
  { key: "name", label: "Name" },
  { key: "starts_at", label: "Starts", width: "7rem" },
  { key: "ends_at", label: "Ends", width: "9rem", render: (row) => `${row.ends_at}${row.crosses_midnight ? " next day" : ""}` },
  { key: "hours", label: "Hours", kind: "quantity", width: "6rem" },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function Shifts() {
  return (
    <ListView<Shift>
      title="Shifts"
      noun={["shift", "shifts"]}
      endpoint="/api/manufacturing/shifts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/shifts/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/shifts/new", permission: "manufacturing.add_shift" }}
    />
  );
}
