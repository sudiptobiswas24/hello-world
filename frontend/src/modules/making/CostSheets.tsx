import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "costed_on", label: "Costed", kind: "date", width: "8rem" },
  { key: "specification_code", label: "Sack" },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
  { key: "cost", label: "Cost", kind: "money", width: "9rem" },
  { key: "price", label: "Price", kind: "money", width: "9rem" },
  { key: "quotation_line", label: "", width: "7rem", render: (row) => (row.quotation_line ? "Quoted" : "") },
];

export default function CostSheets() {
  return (
    <ListView<Row>
      title="Cost sheets"
      noun={["cost sheet", "cost sheets"]}
      endpoint="/api/manufacturing/cost-sheets/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/cost-sheets/${row.id}`}
      searchHint="Specification"
      create={{ href: "/making/cost-sheets/new", permission: "manufacturing.add_costsheet" }}
    />
  );
}
