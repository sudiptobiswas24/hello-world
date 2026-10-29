import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Transfer { id: number; number: string; transfer_date: string; from_name: string; to_name: string; status: string; reference: string; [key: string]: unknown }

const columns: Column<Transfer>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "transfer_date", label: "Date", width: "8rem", sort: "transfer_date", render: (row) => date(row.transfer_date) },
  { key: "from_name", label: "From" },
  { key: "to_name", label: "To" },
  { key: "reference", label: "Reference", width: "10rem" },
  { key: "status", label: "State", width: "8rem", kind: "status" },
];

/** Stock moved between warehouses, in one step or by lorry through transit. */
export default function Transfers() {
  return (
    <ListView<Transfer>
      title="Transfers"
      noun={["transfer", "transfers"]}
      endpoint="/api/inventory/stock-transfers/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/transfers/${row.id}`}
      searchHint="Number, reference"
      facets={[{ label: "In transit", params: { status: "in_transit" } }, { label: "Draft", params: { status: "draft" } }]}
      create={{ href: "/stores/transfers/new", permission: "inventory.add_stocktransfer" }}
    />
  );
}
