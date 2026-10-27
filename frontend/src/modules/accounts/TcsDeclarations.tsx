import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "party_name", label: "Buyer" },
  { key: "form", label: "Form", width: "5rem" },
  { key: "reference", label: "Reference", width: "10rem" },
  { key: "received_on", label: "Received", kind: "date", width: "8rem" },
  { key: "valid_from", label: "From", kind: "date", width: "8rem" },
  { key: "valid_to", label: "To", kind: "date", width: "8rem" },
];

/** Buyers' declarations, on the form the law names for their date, that their scrap is for their own manufacture: no TCS is collected. */
export default function TcsDeclarations() {
  return (
    <ListView<Row>
      title="TCS declarations"
      noun={["declaration", "declarations"]}
      endpoint="/api/accounting/tcs-declarations/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/tcs-declarations/${row.id}`}
      searchHint="Buyer or reference"
      create={{ href: "/accounts/tcs-declarations/new", permission: "accounting.add_tcsdeclaration" }}
    />
  );
}
