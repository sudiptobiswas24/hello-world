import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "date", label: "Paid", kind: "date", width: "8rem" },
  { key: "section_code", label: "Section", width: "6rem" },
  { key: "month", label: "For", width: "8rem", render: (row) => String(row.month ?? "").slice(0, 7) },
  { key: "challan_number", label: "Challan", width: "9rem" },
  { key: "bsr_code", label: "BSR", width: "8rem" },
  { key: "amount", label: "Amount", kind: "money", width: "10rem" },
  { key: "voided", label: "", width: "6rem", render: (row) => (row.voided ? "Voided" : "") },
];

/** Tax paid over to the government, a month and a section at a time. */
export default function TdsChallans() {
  return (
    <ListView<Row>
      title="TDS challans"
      noun={["challan", "challans"]}
      endpoint="/api/purchasing/tds-challans/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/accounts/tds-challans/${row.id}`}
      searchHint="Challan or BSR"
      create={{ href: "/accounts/tds-challans/new", permission: "purchasing.add_tdschallan" }}
    />
  );
}
