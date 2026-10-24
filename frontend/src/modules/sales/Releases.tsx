import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Release { id: number; number: string; inspected_on: string; customer_name: string; order_number: string; agency_name: string;
  their_reference: string; voided_at: string | null; [key: string]: unknown }

const columns: Column<Release>[] = [
  { key: "number", label: "Release", sort: "number", width: "9rem" },
  { key: "inspected_on", label: "Inspected", sort: "inspected_on", width: "8rem", render: (row) => date(row.inspected_on) },
  { key: "customer_name", label: "Customer" },
  { key: "order_number", label: "Order", width: "9rem" },
  { key: "agency_name", label: "Agency" },
  { key: "their_reference", label: "Their certificate", width: "11rem" },
  { key: "voided_at", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : "") },
];

/** An agency's inspection, entered as it is received: which batches it released for which customer. */
export default function Releases() {
  return (
    <ListView<Release>
      title="Third-party releases"
      noun={["release", "releases"]}
      endpoint="/api/sales/third-party-releases/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/releases/${row.id}`}
      searchHint="Release, certificate or customer"
    />
  );
}
