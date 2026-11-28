import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "kind", label: "Figure" },
  { key: "state", label: "State", width: "7rem" },
  { key: "effective_from", label: "From", kind: "date", width: "8rem" },
  { key: "value", label: "Value", kind: "quantity", width: "9rem" },
  { key: "source", label: "Source" },
  { key: "confirmed_on", label: "Confirmed", kind: "date", width: "8rem" },
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
      emptyHint={<>
        Until they are here and confirmed, a pay run, a bill that deducts TDS and an invoice that collects TCS are
        refused. They are loaded from the dated files in statutory/ with <code>manage.py setup_statutory</code>, or
        added one at a time with New, then confirmed here by the Controller.
      </>}
    />
  );
}
