import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "item_label", label: "Item" },
  { key: "rate", label: "Rate", kind: "money" },
  { key: "valid_from", label: "From" },
  { key: "note", label: "Note" },
];

export default function MaterialRates() {
  return (
    <ListView<Row>
      title="Material rates"
      noun={["material rate", "material rates"]}
      endpoint="/api/manufacturing/material-rates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/material-rates/${row.id}`}
      searchHint="Item"
      create={{ href: "/making/material-rates/new", permission: "manufacturing.add_materialrate" }}
    />
  );
}
