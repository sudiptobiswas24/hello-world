import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number; valid_from: string; valid_to: string };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "vendor_name", label: "Supplier" },
  { key: "index_code", label: "Circular", width: "10rem" },
  { key: "discount_per_kg", label: "Off a kg", kind: "quantity", width: "7rem" },
  { key: "valid_from", label: "From", width: "8rem", sort: "valid_from", render: (row) => date(row.valid_from) },
  { key: "valid_to", label: "To", width: "8rem", render: (row) => date(row.valid_to) },
];

/** Polymer bought on a maker's circular, less an agreed discount a kilogramme (L16). */
export default function RateContracts() {
  return (
    <ListView<Row>
      title="Rate contracts"
      noun={["contract", "contracts"]}
      endpoint="/api/purchasing/rate-contracts/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/rate-contracts/${row.id}`}
      searchHint="Number, supplier, circular"
      create={{ href: "/purchasing/rate-contracts/new", permission: "purchasing.add_purchaseratecontract" }}
    />
  );
}
