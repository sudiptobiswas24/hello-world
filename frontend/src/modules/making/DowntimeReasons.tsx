import { ListView, type Column } from "../../views/ListView";

interface Row { id: number; code: string; name: string; is_active: boolean; [key: string]: unknown }

const columns: Column<Row>[] = [
  { key: "code", label: "Code", width: "10rem" },
  { key: "name", label: "Name" },
  { key: "is_planned", label: "Planned", width: "7rem", render: (row) => (row.is_planned ? "Planned" : "") },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function DowntimeReasons() {
  return (
    <ListView<Row>
      title="Stoppage reasons"
      noun={["stoppage reason", "stoppage reasons"]}
      endpoint="/api/manufacturing/downtime-reasons/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/stoppage-reasons/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/stoppage-reasons/new", permission: "manufacturing.add_downtimereason" }}
    />
  );
}
