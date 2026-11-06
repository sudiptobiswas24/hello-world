import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Entry { id: number; number: string; entry_date: string; work_order_number: string; makes: string; machine_code: string;
  quantity_produced: string; quantity_scrapped: string; uom_code: string; lot_code: string; posted: boolean; voided_at: string | null;
  [key: string]: unknown }

const columns: Column<Entry>[] = [
  { key: "number", label: "Entry", sort: "number", width: "9rem" },
  { key: "entry_date", label: "Date", sort: "entry_date", width: "8rem", render: (row) => date(row.entry_date) },
  { key: "work_order_number", label: "Run", width: "9rem" },
  { key: "makes", label: "Made" },
  { key: "machine_code", label: "Machine", width: "8rem" },
  { key: "quantity_produced", label: "Good", kind: "quantity", width: "8rem" },
  { key: "quantity_scrapped", label: "Scrap", kind: "quantity", width: "7rem" },
  { key: "uom_code", label: "", width: "4rem" },
  { key: "lot_code", label: "Batch", width: "9rem" },
  { key: "posted", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : row.posted ? "Posted" : "Draft") },
];

export default function Entries() {
  return (
    <ListView<Entry>
      title="Output"
      noun={["production entry", "production entries"]}
      endpoint="/api/manufacturing/production-entries/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/output/${row.id}`}
      searchHint="Entry, run or item"
      facets={[{ label: "Drafts", params: { posted: "false" } }]}
      create={{ href: "/production/output/new", permission: "manufacturing.add_productionentry" }}
    />
  );
}
