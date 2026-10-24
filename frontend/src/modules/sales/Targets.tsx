import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "who", label: "Team or rep" },
  { key: "period_start", label: "From", kind: "date", sort: "period_start", width: "9rem" },
  { key: "period_end", label: "To", kind: "date", width: "9rem" },
  { key: "amount", label: "Target", kind: "money", sort: "amount", width: "10rem" },
  { key: "note", label: "Note" },
];

/** What each team and rep is to bill, by span. */
export default function Targets() {
  return (
    <ListView<Row>
      title="Sales targets"
      noun={["target", "targets"]}
      endpoint="/api/sales/sales-targets/"
      columns={columns}
      rowHref={(row) => `/sales/targets/${row.id}`}
      searchHint="Team or rep"
      create={{ href: "/sales/targets/new", permission: "sales.add_salestarget" }}
    />
  );
}
