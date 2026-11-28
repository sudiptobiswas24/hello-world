import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Rebatch { id: number; number: string; rebatched_on: string; item_label: string; warehouse_name: string; reason: string;
  lines: unknown[]; voided_at: string | null; [key: string]: unknown }

const columns: Column<Rebatch>[] = [
  { key: "number", label: "Rebatch", sort: "number", width: "9rem" },
  { key: "rebatched_on", label: "Date", sort: "rebatched_on", width: "8rem", render: (row) => date(row.rebatched_on) },
  { key: "item_label", label: "Item" },
  { key: "warehouse_name", label: "Store", width: "9rem" },
  { key: "reason", label: "Why" },
  { key: "voided_at", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : "") },
];

/** Batches split or joined: recorded at the store, posted as they are made. */
export default function Rebatches() {
  return (
    <ListView<Rebatch>
      title="Rebatches"
      noun={["rebatch", "rebatches"]}
      endpoint="/api/manufacturing/rebatches/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/rebatches/${row.id}`}
      searchHint="Rebatch, item or reason"
    />
  );
}
