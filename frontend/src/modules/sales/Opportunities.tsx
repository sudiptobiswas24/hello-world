import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "No.", sort: "created_at", width: "9rem" },
  { key: "customer_name", label: "Customer" },
  { key: "title", label: "Business" },
  { key: "stage", label: "", kind: "status", width: "8rem" },
  { key: "value", label: "Worth", kind: "money", sort: "value", width: "10rem" },
  { key: "chance", label: "Chance %", width: "7rem", render: (row) => String(row.chance ?? "") },
  { key: "weighted_value", label: "Weighted", kind: "money", width: "10rem" },
  { key: "expected_on", label: "Expected", kind: "date", sort: "expected_on", width: "8rem" },
  { key: "owner_name", label: "Rep", width: "10rem" },
];

/** Business in play, a stage and a chance each: what the rep carries toward an order. */
export default function Opportunities() {
  return (
    <ListView<Row>
      title="Opportunities"
      noun={["opportunity", "opportunities"]}
      endpoint="/api/sales/opportunities/"
      columns={columns}
      facets={[
        { label: "New", params: { stage: "new" } },
        { label: "Qualified", params: { stage: "qualified" } },
        { label: "Quoted", params: { stage: "quoted" } },
        { label: "Won", params: { stage: "won" } },
        { label: "Lost", params: { stage: "lost" } },
      ]}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/opportunities/${row.id}`}
      searchHint="Number, customer or business"
      create={{ href: "/sales/opportunities/new", permission: "sales.add_opportunity" }}
    />
  );
}
