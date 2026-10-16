import { ListView, type Column } from "../../views/ListView";

interface Rule { id: number; work_centre_name: string; from_family: string; to_family: string; minutes: string;
  purge_kg: string; [key: string]: unknown }

const columns: Column<Rule>[] = [
  { key: "work_centre_name", label: "On" },
  { key: "from_family", label: "From", width: "12rem", render: (row) => row.from_family || "Any" },
  { key: "to_family", label: "To", width: "12rem", render: (row) => row.to_family || "Any" },
  { key: "minutes", label: "Minutes", kind: "quantity", width: "8rem" },
  { key: "purge_kg", label: "Purge kg", kind: "quantity", width: "8rem" },
];

export default function ChangeoverRules() {
  return (
    <ListView<Rule>
      title="Changeover rules"
      noun={["changeover rule", "changeover rules"]}
      endpoint="/api/manufacturing/changeover-rules/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/changeovers/${row.id}`}
      create={{ href: "/making/changeovers/new", permission: "manufacturing.add_changeoverrule" }}
    />
  );
}
