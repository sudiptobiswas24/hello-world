import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem" },
  { key: "date", label: "Issued", kind: "date", width: "8rem" },
  { key: "bill_number", label: "Bill", width: "10rem" },
  { key: "supplier_name", label: "Supplier" },
  { key: "taxable", label: "Taxable", kind: "money", width: "9rem" },
  { key: "igst", label: "IGST", kind: "money", width: "8rem" },
  { key: "cgst", label: "CGST", kind: "money", width: "8rem" },
  { key: "sgst", label: "SGST", kind: "money", width: "8rem" },
  { key: "cancelled_on", label: "", width: "7rem", render: (row) => (row.cancelled_on ? "Cancelled" : "") },
];

/** Self-invoices for supplies under reverse charge from suppliers not registered (s.31(3)(f)). */
export default function SelfInvoices() {
  return (
    <ListView<Row>
      title="Self-invoices"
      noun={["self-invoice", "self-invoices"]}
      endpoint="/api/gst/self-invoices/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/self-invoices/${row.id}`}
      searchHint="Number, bill or supplier"
      create={{ href: "/accounts/self-invoices/new", permission: "gst.add_selfinvoice" }}
    />
  );
}
