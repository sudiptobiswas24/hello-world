import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "stage", label: "Stage" },
  { key: "work_centre_name", label: "Bank" },
  { key: "rate", label: "Rate", kind: "money" },
  { key: "valid_from", label: "From" },
  { key: "note", label: "Note" },
];

export default function StageRates() {
  return (
    <ListView<Row>
      title="Conversion rates"
      noun={["conversion rate", "conversion rates"]}
      endpoint="/api/manufacturing/stage-rates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/stage-rates/${row.id}`}
      searchHint="Stage"
      create={{ href: "/making/stage-rates/new", permission: "manufacturing.add_stagerate" }}
    />
  );
}
