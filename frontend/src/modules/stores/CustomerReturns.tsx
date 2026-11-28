import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";
import { postedLabel } from "./partyRefs";

interface Return { id: number; number: string; returned_on: string; customer_name: string; warehouse_name: string;
  lines: unknown[]; posted: boolean; voided_at: string | null; [key: string]: unknown }

const columns: Column<Return>[] = [
  { key: "number", label: "Return", sort: "number", width: "10rem" },
  { key: "returned_on", label: "Returned", sort: "returned_on", width: "8rem", render: (row) => date(row.returned_on) },
  { key: "customer_name", label: "Customer" },
  { key: "warehouse_name", label: "From", width: "9rem" },
  { key: "lines", label: "Lines", width: "5rem", render: (row) => String(row.lines.length) },
  { key: "posted", label: "", width: "6rem", render: postedLabel },
];

export default function CustomerReturns() {
  return (
    <ListView<Return>
      title="Customer material back"
      noun={["customer material return", "customer material returns"]}
      endpoint="/api/manufacturing/customer-material-returns/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/customer-material-back/${row.id}`}
      searchHint="Return or customer"
      create={{ href: "/stores/customer-material-back/new", permission: "manufacturing.add_customermaterialreturn" }}
    />
  );
}
