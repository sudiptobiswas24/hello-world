import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "bag_width_cm", label: "Width cm", kind: "quantity" },
  { key: "bag_length_cm", label: "Length cm", kind: "quantity" },
  { key: "print_colours", label: "Colours", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function BagSpecs() {
  return (
    <ListView<Row>
      title="Bag specifications"
      noun={["bag specification", "bag specifications"]}
      endpoint="/api/manufacturing/bag-specifications/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/bags/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/bags/new", permission: "manufacturing.add_bagspecification" }}
    />
  );
}
