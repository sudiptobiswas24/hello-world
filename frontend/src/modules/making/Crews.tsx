import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Crew { id: number; employee_name: string; work_centre_name: string; shift_name: string; valid_from: string;
  valid_to: string | null; [key: string]: unknown }

const columns: Column<Crew>[] = [
  { key: "employee_name", label: "Who" },
  { key: "work_centre_name", label: "Bank" },
  { key: "shift_name", label: "Shift", width: "10rem" },
  { key: "valid_from", label: "From", width: "8rem", render: (row) => date(row.valid_from) },
  { key: "valid_to", label: "To", width: "8rem", render: (row) => date(row.valid_to) },
];

export default function Crews() {
  return (
    <ListView<Crew>
      title="Crews"
      noun={["crew assignment", "crew assignments"]}
      endpoint="/api/manufacturing/crew-assignments/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/crews/${row.id}`}
      create={{ href: "/making/crews/new", permission: "manufacturing.add_crewassignment" }}
    />
  );
}
