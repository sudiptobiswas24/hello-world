import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "cut_length_cm", label: "Cut length cm", kind: "quantity" },
  { key: "liner_grams", label: "Grams", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function LinerSpecs() {
  return (
    <ListView<Row>
      title="Liner specifications"
      noun={["liner specification", "liner specifications"]}
      endpoint="/api/manufacturing/liner-specifications/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/liners/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/liners/new", permission: "manufacturing.add_linerspecification" }}
    />
  );
}
