import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "name", label: "Who", sort: "name" },
  { key: "opening_title", label: "For" },
  { key: "applied_on", label: "Applied", kind: "date", sort: "applied_on", width: "8rem" },
  { key: "source", label: "From", kind: "status", width: "8rem" },
  { key: "rating", label: "Rating", width: "6rem", render: (row) => (row.rating == null ? "—" : `${String(row.rating)} / 5`) },
  { key: "stage", label: "Stage", kind: "status", sort: "stage", width: "8rem" },
];

/** Everyone who applied, at whatever stage; hired, they are on the rolls. */
export default function Applicants() {
  return (
    <ListView<Row>
      title="Applicants"
      noun={["applicant", "applicants"]}
      endpoint="/api/hr/applicants/"
      columns={columns}
      rowHref={(row) => `/payroll/applicants/${row.id}`}
      searchHint="Name, phone, email or job"
      facets={[{ label: "Interviewing", params: { stage: "interview" } }, { label: "Offered", params: { stage: "offered" } }]}
      create={{ href: "/payroll/applicants/new", permission: "hr.add_applicant" }}
    />
  );
}
