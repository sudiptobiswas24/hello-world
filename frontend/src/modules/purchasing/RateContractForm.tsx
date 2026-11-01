import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

const PARTY = { endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" },
  label: (row: Row) => String(row.name) };
const INDEX = { endpoint: "/api/sales/price-indices/", permission: "sales.view_priceindex",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const ITEM = { endpoint: "/api/inventory/items/", permission: "inventory.view_item",
  label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };

/** A rate contract: the supplier, the circular, the discount, the period, and what may be ordered on it. */
export default function RateContractForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/rate-contracts/"
      back="/purchasing/rate-contracts"
      backLabel="Rate contracts"
      newTitle="New rate contract"
      heading={(row) => `${String(row.number || "Rate contract")} · ${String(row.vendor_name ?? "")}`}
      permissions={{ add: "purchasing.add_purchaseratecontract", change: "purchasing.change_purchaseratecontract",
        delete: "purchasing.delete_purchaseratecontract" }}
      fields={[
        { key: "vendor", label: "Supplier", kind: "pick", pick: PARTY, show: (row: Row) => String(row.vendor_name ?? "—") },
        { key: "index", label: "Circular (price index)", kind: "pick", pick: INDEX, show: (row: Row) => String(row.index_code ?? "—") },
        { key: "discount_per_kg", label: "Discount a kg", kind: "decimal", places: 4, initial: "0" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "valid_to", label: "To", kind: "date" },
        { key: "note", label: "Note", kind: "text", wide: true },
        { key: "index_today", label: "Circular today", kind: "decimal", readOnly: true, existingOnly: true },
      ]}
      panels={[{
        title: "Items", permission: "purchasing.view_ratecontractline", endpoint: "/api/purchasing/rate-contract-lines/",
        query: (row) => ({ contract: String(row.id) }),
        columns: [
          { key: "item_name", label: "Item" },
          { key: "quantity_kg", label: "Agreed kg", kind: "quantity", width: "10rem" },
          { key: "ordered_kg", label: "Ordered kg", kind: "quantity", width: "10rem" },
        ],
        adder: { label: "Add an item", permission: "purchasing.add_ratecontractline",
          url: () => "/api/purchasing/rate-contract-lines/",
          fields: [{ key: "item", label: "Item", kind: "pick", pick: ITEM }, { key: "quantity_kg", label: "Kilogrammes", kind: "decimal" }],
          body: (values, row) => ({ ...values, contract: row.id }) },
        remover: { permission: "purchasing.delete_ratecontractline", url: (line) => `/api/purchasing/rate-contract-lines/${String(line.id)}/` },
      }]}
    />
  );
}
