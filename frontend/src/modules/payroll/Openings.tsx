import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "title", label: "Job", sort: "title" },
  { key: "department_name", label: "Department" },
  { key: "openings", label: "Wanted", kind: "quantity", width: "6rem" },
  { key: "hired", label: "Hired", kind: "quantity", width: "6rem" },
  { key: "applicant_count", label: "Applicants", kind: "quantity", width: "7rem" },
  { key: "opened_on", label: "Opened", kind: "date", sort: "opened_on", width: "8rem" },
  { key: "status", label: "Status", kind: "status", width: "8rem" },
];

/** What jobs are wanted and how many, with who has applied. */
export default function Openings() {
  return (
    <ListView<Row>
      title="Job openings"
      noun={["opening", "openings"]}
      endpoint="/api/hr/job-openings/"
      columns={columns}
      rowHref={(row) => `/payroll/openings/${row.id}`}
      searchHint="Job"
      facets={[{ label: "Open", params: { status: "open" } }]}
      create={{ href: "/payroll/openings/new", permission: "hr.add_jobopening" }}
    />
  );
}
