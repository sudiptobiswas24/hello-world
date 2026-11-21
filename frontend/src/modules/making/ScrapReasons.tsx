import { ListView, type Column } from "../../views/ListView";

interface Row { id: number; code: string; name: string; is_active: boolean; [key: string]: unknown }

const columns: Column<Row>[] = [
  { key: "code", label: "Code", width: "10rem" },
  { key: "name", label: "Name" },
  { key: "is_active", label: "", width: "7rem", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function ScrapReasons() {
  return (
    <ListView<Row>
      title="Scrap reasons"
      noun={["scrap reason", "scrap reasons"]}
      endpoint="/api/manufacturing/scrap-reasons/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/scrap-reasons/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/scrap-reasons/new", permission: "manufacturing.add_scrapreason" }}
    />
  );
}
