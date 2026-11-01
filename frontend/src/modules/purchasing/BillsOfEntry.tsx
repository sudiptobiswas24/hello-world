import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number; be_date: string };

const columns: Column<Row>[] = [
  { key: "number", label: "Ours", width: "10rem", sort: "number" },
  { key: "be_number", label: "BE number", width: "8rem", sort: "be_number" },
  { key: "be_date", label: "BE date", width: "8rem", sort: "be_date", render: (row) => date(row.be_date) },
  { key: "port_code", label: "Port", width: "6rem" },
  { key: "assessable_total", label: "Assessable", kind: "money", width: "10rem" },
  { key: "duty_total", label: "Duty and SWS", kind: "money", width: "9rem" },
  { key: "igst_total", label: "IGST", kind: "money", width: "9rem" },
  { key: "status", label: "State", width: "8rem", kind: "status" },
];

/** Goods imported, as customs assessed them: duty landed on the goods, IGST to input credit (L21). */
export default function BillsOfEntry() {
  return (
    <ListView<Row>
      title="Bills of entry"
      noun={["bill of entry", "bills of entry"]}
      endpoint="/api/purchasing/bills-of-entry/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/bills-of-entry/${row.id}`}
      searchHint="BE number, ours, port"
      facets={[{ label: "Draft", params: { status: "draft" } }, { label: "Posted", params: { status: "posted" } }]}
      create={{ href: "/purchasing/bills-of-entry/new", permission: "purchasing.add_billofentry" }}
    />
  );
}
