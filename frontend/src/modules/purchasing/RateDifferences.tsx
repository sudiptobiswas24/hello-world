import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number; note_date: string };

const columns: Column<Row>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "note_date", label: "Dated", width: "8rem", sort: "note_date", render: (row) => date(row.note_date) },
  { key: "vendor_name", label: "Supplier" },
  { key: "supplier_reference", label: "Their note", width: "10rem" },
  { key: "total", label: "Difference", kind: "money", width: "9rem" },
  { key: "status", label: "State", width: "8rem", kind: "status" },
];

/** A circular revised back-dated: the supplier's rate-difference notes, landed on what was received (L16). */
export default function RateDifferences() {
  return (
    <ListView<Row>
      title="Rate-difference notes"
      noun={["note", "notes"]}
      endpoint="/api/purchasing/rate-differences/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/rate-differences/${row.id}`}
      searchHint="Number, supplier, their note"
      facets={[{ label: "Draft", params: { status: "draft" } }]}
      create={{ href: "/purchasing/rate-differences/new", permission: "purchasing.add_ratedifferencenote" }}
    />
  );
}
