import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "state", label: "State", width: "6rem" },
  { key: "employment", label: "Scheduled employment" },
  { key: "zone", label: "Zone", width: "5rem" },
  { key: "skill", label: "Skill", width: "8rem" },
  { key: "effective_from", label: "From", kind: "date", width: "8rem" },
  { key: "monthly", label: "A month", kind: "money", width: "9rem" },
  { key: "confirmed_on", label: "Confirmed", kind: "date", width: "8rem" },
];

/** The State's minimum wages by employment, zone and skill, each VDA revision a row from its day: the bonus ceiling reads them. */
export default function MinimumWages() {
  return (
    <ListView<Row>
      title="Minimum wages"
      noun={["minimum wage", "minimum wages"]}
      endpoint="/api/hr/minimum-wages/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/minimum-wages/${row.id}`}
      create={{ href: "/payroll/minimum-wages/new", permission: "hr.add_minimumwage" }}
    />
  );
}
