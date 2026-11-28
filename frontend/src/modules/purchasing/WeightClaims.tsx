import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Claim { id: number; number: string; raised_on: string; receipt_number: string; vendor_name: string; against: string; quantity: string; value: string; status: string; [key: string]: unknown }

const columns: Column<Claim>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "raised_on", label: "Raised", width: "8rem", sort: "raised_on", render: (row) => date(row.raised_on) },
  { key: "receipt_number", label: "Receipt", width: "10rem" },
  { key: "vendor_name", label: "Supplier" },
  { key: "against", label: "Against", width: "8rem" },
  { key: "quantity", label: "Short", kind: "quantity", width: "8rem" },
  { key: "value", label: "Value", kind: "money", width: "9rem" },
  { key: "status", label: "State", width: "8rem", kind: "status" },
];

/** Goods short on the weighbridge, claimed from the supplier or the transporter. */
export default function WeightClaims() {
  return (
    <ListView<Claim>
      title="Weight claims"
      noun={["claim", "claims"]}
      endpoint="/api/purchasing/weight-claims/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/weight-claims/${row.id}`}
      searchHint="Number, receipt, supplier"
      facets={[{ label: "Open", params: { status: "open" } }]}
      create={{ href: "/purchasing/weight-claims/new", permission: "purchasing.add_weightclaim" }}
    />
  );
}
