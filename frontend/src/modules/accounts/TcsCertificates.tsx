import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "party_name", label: "Buyer" },
  { key: "reference", label: "Certificate", width: "10rem" },
  { key: "rate_percent", label: "Rate %", width: "6rem" },
  { key: "amount", label: "Up to", kind: "money", width: "10rem" },
  { key: "left", label: "Left", kind: "money", width: "10rem" },
  { key: "valid_from", label: "From", kind: "date", width: "8rem" },
  { key: "valid_to", label: "To", kind: "date", width: "8rem" },
];

/** Buyers' certificates for TCS at a lower rate (section 206C(9)): their rate up to the amount each states, the section's past it. */
export default function TcsCertificates() {
  return (
    <ListView<Row>
      title="TCS certificates"
      noun={["certificate", "certificates"]}
      endpoint="/api/accounting/tcs-certificates/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/tcs-certificates/${row.id}`}
      searchHint="Buyer or certificate"
      create={{ href: "/accounts/tcs-certificates/new", permission: "accounting.add_tcscertificate" }}
    />
  );
}
