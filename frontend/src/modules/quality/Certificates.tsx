import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Certificate { id: number; number: string; issued_on: string; delivery_number: string; customer_name: string;
  voided_at: string | null; [key: string]: unknown }

const columns: Column<Certificate>[] = [
  { key: "number", label: "Certificate", sort: "number", width: "10rem" },
  { key: "issued_on", label: "Issued", sort: "issued_on", width: "8rem", render: (row) => date(row.issued_on) },
  { key: "delivery_number", label: "For delivery", width: "10rem" },
  { key: "customer_name", label: "Customer" },
  { key: "voided_at", label: "", width: "7rem", render: (row) => (row.voided_at ? "Withdrawn" : "") },
];

export default function Certificates() {
  return (
    <ListView<Certificate>
      title="Test certificates"
      noun={["test certificate", "test certificates"]}
      endpoint="/api/manufacturing/test-certificates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/certificates/${row.id}`}
      searchHint="Certificate, delivery or customer"
      create={{ href: "/quality/certificates/new", permission: "manufacturing.add_testcertificate" }}
    />
  );
}
