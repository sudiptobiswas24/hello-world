import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "employee", label: "Former employee", width: "9rem" },
  { key: "kind", label: "Settled by", width: "8rem" },
  { key: "amount", label: "Amount", kind: "money", width: "9rem" },
  { key: "owed_back", label: "Still owed", kind: "money", width: "9rem" },
  { key: "date", label: "On", kind: "date", width: "8rem" },
  { key: "reason", label: "Why written off" },
];

/** What a leaver's final pay could not take back, paid back or written off (O215). */
export default function ShortfallSettlements() {
  return (
    <ListView<Row>
      title="Owed back by former employees"
      noun={["settlement", "settlements"]}
      endpoint="/api/hr/shortfall-settlements/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/owed-back/${row.id}`}
      create={{ href: "/payroll/owed-back/new", permission: "hr.add_shortfallsettlement" }}
    />
  );
}
