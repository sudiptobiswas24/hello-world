import { money } from "../../lib/format";
import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };

/** What is in play by stage, at its value and at its chance: the rep's own, or everyone's for whoever sees everyone's. */
export default function Pipeline() {
  return (
    <ReportView<Row[], Row>
      title="Pipeline"
      endpoint="/api/sales/opportunities/pipeline/"
      params={[]}
      rows={(data) => data.map((row, index) => ({ ...row, id: index }))}
      columns={[
        { key: "label", label: "Stage", width: "10rem" },
        { key: "count", label: "How many", kind: "quantity", width: "8rem", render: (row) => String(row.count) },
        { key: "value", label: "Worth", kind: "money", width: "12rem", render: (row) => money(row.value as string) },
        { key: "weighted", label: "At its chance", kind: "money", width: "12rem", render: (row) => money(row.weighted as string) },
      ]}
      empty="Nothing in play."
    />
  );
}
