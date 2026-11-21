import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "date", label: "Date", kind: "date", width: "8rem" },
  { key: "section_code", label: "Section", width: "6rem" },
  { key: "vendor_name", label: "Vendor" },
  { key: "bill_number", label: "Bill", width: "10rem" },
  { key: "base", label: "On", kind: "money", width: "10rem" },
  { key: "amount", label: "Deducted", kind: "money", width: "9rem" },
  { key: "challan_label", label: "Paid over", width: "10rem", render: (row) => (row.reversed ? "Reversed" : String(row.challan_label || "Not yet")) },
];

/** Tax deducted from what vendors are paid: made on the bill, paid over by challan. */
export default function TdsDeductions() {
  return (
    <ListView<Row>
      title="TDS deducted"
      noun={["deduction", "deductions"]}
      endpoint="/api/purchasing/tds-deductions/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/tds-deducted/${row.id}`}
      searchHint="Bill, vendor or PAN"
      facets={[{ label: "Standing", params: { reversed_entry__isnull: "true" } }]}
    />
  );
}
