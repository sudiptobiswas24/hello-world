import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "code", label: "Code" },
  { key: "name", label: "Name" },
  { key: "ends_per_inch", label: "Ends", kind: "quantity" },
  { key: "picks_per_inch", label: "Picks", kind: "quantity" },
  { key: "gsm", label: "GSM", kind: "quantity" },
  { key: "is_active", label: "Active", render: (row) => (row.is_active ? "" : "Inactive") },
];

export default function FabricSpecs() {
  return (
    <ListView<Row>
      title="Fabric specifications"
      noun={["fabric specification", "fabric specifications"]}
      endpoint="/api/manufacturing/fabric-specifications/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/fabrics/${row.id}`}
      searchHint="Code or name"
      create={{ href: "/making/fabrics/new", permission: "manufacturing.add_fabricspecification" }}
    />
  );
}
