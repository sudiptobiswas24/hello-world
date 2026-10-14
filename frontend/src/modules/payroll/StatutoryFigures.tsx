import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "kind", label: "Figure" },
  { key: "state", label: "State", width: "7rem" },
  { key: "effective_from", label: "From", kind: "date", width: "8rem" },
  { key: "value", label: "Value", kind: "quantity", width: "9rem" },
  { key: "source", label: "Source" },
];

/** The figures the law sets, dated: a run keeps the one that stood on its last day. */
export default function StatutoryFigures() {
  return (
    <ListView<Row>
      title="Statutory figures"
      noun={["figure", "figures"]}
      endpoint="/api/hr/statutory-figures/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/payroll/statutory-figures/${row.id}`}
      create={{ href: "/payroll/statutory-figures/new", permission: "hr.add_statutoryfigure" }}
    />
  );
}
