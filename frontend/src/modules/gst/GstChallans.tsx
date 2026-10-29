import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "date", label: "Paid", kind: "date", width: "8rem" },
  { key: "month", label: "For", width: "8rem", render: (row) => String(row.month ?? "").slice(0, 7) },
  { key: "cpin", label: "CPIN", width: "11rem" },
  { key: "igst", label: "IGST", kind: "money", width: "9rem" },
  { key: "cgst", label: "CGST", kind: "money", width: "9rem" },
  { key: "sgst", label: "SGST", kind: "money", width: "9rem" },
  { key: "cess", label: "Cess", kind: "money", width: "8rem" },
  { key: "voided", label: "", width: "6rem", render: (row) => (row.voided ? "Voided" : "") },
];

/** Reverse-charge tax paid in cash, a month at a time, by challan (PMT-06). */
export default function GstChallans() {
  return (
    <ListView<Row>
      title="GST cash challans"
      noun={["challan", "challans"]}
      endpoint="/api/gst/cash-payments/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/gst-challans/${row.id}`}
      searchHint="CPIN"
      create={{ href: "/accounts/gst-challans/new", permission: "gst.add_gstcashpayment" }}
    />
  );
}
