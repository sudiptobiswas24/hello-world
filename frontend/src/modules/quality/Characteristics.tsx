import { ListView, type Column } from "../../views/ListView";

interface Characteristic { id: number; code: string; name: string; kind: string; uom_code: string; is_active: boolean; [key: string]: unknown }

const columns: Column<Characteristic>[] = [
  { key: "code", label: "Code", width: "9rem", sort: "code" },
  { key: "name", label: "What is tested" },
  { key: "kind", label: "Kind", width: "12rem", render: (row) => (row.kind === "measured" ? "Measured" : "Present or absent") },
  { key: "uom_code", label: "Unit", width: "6rem" },
];

export default function Characteristics() {
  return (
    <ListView<Characteristic>
      title="Characteristics"
      noun={["characteristic", "characteristics"]}
      endpoint="/api/quality/characteristics/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/characteristics/${row.id}`}
      searchHint="Code, name"
      create={{ href: "/quality/characteristics/new", permission: "quality.add_characteristic" }}
    />
  );
}
