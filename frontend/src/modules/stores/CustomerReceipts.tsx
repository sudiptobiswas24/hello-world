import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";
import { postedLabel } from "./partyRefs";

interface Receipt { id: number; number: string; received_on: string; customer_name: string; their_challan: string;
  warehouse_name: string; posted: boolean; voided_at: string | null; [key: string]: unknown }

const columns: Column<Receipt>[] = [
  { key: "number", label: "Receipt", sort: "number", width: "10rem" },
  { key: "received_on", label: "Received", sort: "received_on", width: "8rem", render: (row) => date(row.received_on) },
  { key: "customer_name", label: "Customer" },
  { key: "their_challan", label: "Their challan", width: "10rem" },
  { key: "warehouse_name", label: "Store", width: "9rem" },
  { key: "posted", label: "", width: "6rem", render: postedLabel },
];

export default function CustomerReceipts() {
  return (
    <ListView<Receipt>
      title="Customer material in"
      noun={["customer material receipt", "customer material receipts"]}
      endpoint="/api/manufacturing/customer-material-receipts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/customer-material/${row.id}`}
      searchHint="Receipt, challan or customer"
      create={{ href: "/stores/customer-material/new", permission: "manufacturing.add_customermaterialreceipt" }}
    />
  );
}
