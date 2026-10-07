import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "employee_name", label: "Who" },
  { key: "reviewer_name", label: "By" },
  { key: "period_start", label: "From", kind: "date", sort: "period_start", width: "8rem" },
  { key: "period_end", label: "To", kind: "date", width: "8rem" },
  { key: "rating", label: "Rating", width: "6rem", render: (row) => (row.rating == null ? "—" : `${String(row.rating)} / 5`) },
  { key: "status", label: "Status", kind: "status", width: "9rem" },
];

/** A reviewer's reading of a person over a span, acknowledged by the person. */
export default function Appraisals() {
  return (
    <ListView<Row>
      title="Appraisals"
      noun={["appraisal", "appraisals"]}
      endpoint="/api/hr/appraisals/"
      columns={columns}
      rowHref={(row) => `/payroll/appraisals/${row.id}`}
      searchHint="Who"
      facets={[{ label: "Drafts", params: { status: "draft" } }, { label: "To acknowledge", params: { status: "submitted" } }]}
      create={{ href: "/payroll/appraisals/new", permission: "hr.add_appraisal" }}
    />
  );
}
