import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };
const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" }, label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };

/** A price a vendor has agreed for an item, from a quantity and for a period: what an order line is checked against, and who planning buys from by default. */
export default function VendorPriceForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/vendor-prices/"
      back="/purchasing/vendor-prices"
      backLabel="Vendor prices"
      newTitle="New vendor price"
      heading={(row) => String(row.vendor_name ?? "") + " · " + String(row.item_label ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "purchasing.add_vendorprice", change: "purchasing.change_vendorprice", delete: "purchasing.delete_vendorprice" }}
      fields={[
        { key: "vendor", label: "Vendor", kind: "pick", pick: PARTY },
        { key: "item", label: "Item", kind: "pick", pick: ITEM },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY },
        { key: "unit_price", label: "Unit price", kind: "money" },
        { key: "min_quantity", label: "From quantity", kind: "decimal", initial: "0", hint: "Smallest order this price applies to — the quantity break" },
        { key: "vendor_item_code", label: "Vendor item code", hint: "The vendor's own part number, which is what their invoice will quote" },
        { key: "lead_time_days", label: "Lead time, days", kind: "integer", hint: "Days from order to delivery, as agreed" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "valid_to", label: "To", kind: "date" },
        { key: "is_preferred", label: "Preferred", kind: "bool", initial: false, hint: "Buy this item from this vendor by default" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
