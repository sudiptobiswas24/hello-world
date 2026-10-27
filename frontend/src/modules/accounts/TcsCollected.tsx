import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "date", label: "Date", kind: "date", width: "8rem" },
  { key: "section_code", label: "Section", width: "6rem" },
  { key: "invoice_number", label: "Invoice", width: "10rem" },
  { key: "buyer_name", label: "Buyer" },
  { key: "base", label: "On", kind: "money", width: "10rem" },
  { key: "amount", label: "TCS", kind: "money", width: "8rem" },
  { key: "gives_back", label: "", width: "7rem", render: (row) => (row.gives_back ? "Given back" : "Collected") },
  { key: "challan", label: "", width: "10rem",
    render: (row) => (row.declaration ? "Declared: not collected" : row.challan ? "Paid over" : "Due") },
];

/** What each scrap invoice collected and each credit note gave back, and whether a challan has paid it over. */
export default function TcsCollected() {
  return (
    <ListView<Row>
      title="TCS collected"
      noun={["collection", "collections"]}
      endpoint="/api/sales/tcs-collections/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/sales/invoices/${String(row.invoice)}`}
      searchHint="Invoice, buyer or PAN"
    />
  );
}
