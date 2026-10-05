import { date, quantity } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Lot { id: number; code: string; item_label: string; expires_on: string | null; on_hand: string; has_expired: boolean; supplier_reference: string; [key: string]: unknown }

const columns: Column<Lot>[] = [
  { key: "code", label: "Batch", width: "11rem", sort: "code" },
  { key: "item_label", label: "Item" },
  { key: "supplier_reference", label: "Supplier's batch", width: "11rem" },
  { key: "on_hand", label: "On hand", kind: "quantity", width: "9rem", render: (row) => quantity(row.on_hand) },
  { key: "expires_on", label: "Expires", width: "8rem", render: (row) => `${date(row.expires_on)}${row.has_expired ? " (expired)" : ""}` },
];

/** Batches: granule lots in, tape and fabric rolls and bales made. */
export default function Lots() {
  return (
    <ListView<Lot>
      title="Batches"
      noun={["batch", "batches"]}
      endpoint="/api/inventory/lots/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/stores/batches/${row.id}`}
      searchHint="Batch, item, supplier's batch"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
    />
  );
}
