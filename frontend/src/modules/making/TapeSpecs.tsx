import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "denier", label: "Denier", kind: "quantity" },
  { key: "tape_width_mm", label: "Width mm", kind: "quantity" },
  { key: "virgin_percent", label: "Virgin %", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function TapeSpecs() {
  return (
    <ListView<Row>
      title="Tape specifications"
      noun={["tape specification", "tape specifications"]}
      endpoint="/api/manufacturing/tape-specifications/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/tapes/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/tapes/new", permission: "manufacturing.add_tapespecification" }}
    />
  );
}
