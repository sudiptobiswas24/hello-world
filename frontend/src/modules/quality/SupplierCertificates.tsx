import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Certificate { id: number; number: string; recorded_on: string; lot_code: string; item_label: string;
  vendor_name: string; supplier_number: string; result: string; posted: boolean; voided_at: string | null; [key: string]: unknown }

const columns: Column<Certificate>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "recorded_on", label: "Read in", width: "8rem", sort: "recorded_on", render: (row) => date(row.recorded_on) },
  { key: "lot_code", label: "Batch", width: "10rem" },
  { key: "item_label", label: "Item" },
  { key: "vendor_name", label: "Supplier" },
  { key: "supplier_number", label: "Their number", width: "10rem" },
  { key: "result", label: "", width: "8rem",
    render: (row) => (row.voided_at ? "Voided" : !row.posted ? "Draft" : row.result === "within" ? "Within" : "Outside") },
];

/** The suppliers' certificates of analysis for batches bought in, read against the plan of the day each came in. */
export default function SupplierCertificates() {
  return (
    <ListView<Certificate>
      title="Supplier certificates"
      noun={["supplier certificate", "supplier certificates"]}
      endpoint="/api/quality/supplier-certificates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/supplier-certificates/${row.id}`}
      searchHint="Number, their number, batch, supplier"
      facets={[{ label: "Draft", params: { posted: "false" } }, { label: "Outside", params: { result: "outside" } }]}
      create={{ href: "/quality/supplier-certificates/new", permission: "quality.add_suppliercertificate" }}
    />
  );
}
