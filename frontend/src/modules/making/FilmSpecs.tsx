import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "micron", label: "Micron", kind: "quantity" },
  { key: "lay_flat_width_cm", label: "Lay-flat cm", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function FilmSpecs() {
  return (
    <ListView<Row>
      title="Film specifications"
      noun={["film specification", "film specifications"]}
      endpoint="/api/manufacturing/film-specifications/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/films/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/films/new", permission: "manufacturing.add_filmspecification" }}
    />
  );
}
