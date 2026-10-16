import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Bale { id: number; number: string; item: string; warehouse: string; packed_on: string; packed_by: string; status: string;
  bags: string; nominal_kg: string; gross_kg: string | null; delivery: string | null; [key: string]: unknown }

const columns: Column<Bale>[] = [
  { key: "number", label: "Bale", width: "10rem" },
  { key: "item", label: "Of", width: "12rem" },
  { key: "packed_on", label: "Packed", width: "8rem", render: (row) => date(row.packed_on) },
  { key: "bags", label: "Bags", kind: "quantity", width: "7rem" },
  { key: "nominal_kg", label: "Kg", kind: "quantity", width: "8rem" },
  { key: "warehouse", label: "Store", width: "7rem" },
  { key: "delivery", label: "Shipped on", width: "10rem", render: (row) => row.delivery ?? "" },
  { key: "status", label: "", width: "8rem", kind: "status" },
];

/** Bales as packed: the most recent two hundred, with where each went. */
export default function Bales() {
  return (
    <ListView<Bale>
      title="Bales"
      noun={["bale", "bales"]}
      endpoint="/api/manufacturing/bales/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/bales/${row.id}`}
    />
  );
}
