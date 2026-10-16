import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "reported_on", label: "Day", kind: "date", width: "8rem" },
  { key: "step", label: "Step" },
  { key: "quantity_good", label: "Passed on", kind: "quantity", width: "9rem" },
  { key: "memo", label: "Note" },
  { key: "voided_at", label: "", width: "7rem", render: (row) => (row.voided_at ? "Voided" : "") },
];

export default function StepCounts() {
  return (
    <ListView<Row>
      title="Step counts"
      noun={["count", "counts"]}
      endpoint="/api/manufacturing/operation-reports/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/step-counts/${row.id}`}
      searchHint="Run, step or note"
      create={{ href: "/production/step-counts/new", permission: "manufacturing.add_operationreport" }}
    />
  );
}
