import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { ITEM } from "./extraRefs";

type Row = Record<string, unknown>;

const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };

/** Prices by item and quantity in one currency, for a span of dates. */
export default function PriceListForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/price-lists/"
      back="/sales/price-lists"
      backLabel="Price lists"
      newTitle="New price list"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "sales.add_pricelist", change: "sales.change_pricelist", delete: "sales.delete_pricelist" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY },
        { key: "is_default", label: "Default", kind: "bool", initial: false, hint: "Used for any customer without a list of their own" },
        { key: "valid_from", label: "From", kind: "date" },
        { key: "valid_to", label: "To", kind: "date" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        // A price from a quantity up: the order line takes the best break
        // its quantity reaches.
        title: "Prices", permission: "sales.view_pricelistitem", endpoint: "/api/sales/price-list-items/",
        query: (list) => ({ price_list: String(list.id) }),
        columns: [
          { key: "item_label", label: "Item" },
          { key: "min_quantity", label: "From quantity", kind: "quantity", width: "9rem" },
          { key: "unit_price", label: "Unit price", kind: "money", width: "9rem" },
        ],
        adder: { label: "Add a price", permission: "sales.add_pricelistitem", url: () => "/api/sales/price-list-items/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "min_quantity", label: "From quantity", kind: "decimal", initial: "1" },
            { key: "unit_price", label: "Unit price", kind: "money" },
          ],
          body: (values, list) => ({ ...values, price_list: list.id }) },
        remover: { permission: "sales.delete_pricelistitem", url: (row) => `/api/sales/price-list-items/${String(row.id)}/` },
      }]}
    />
  );
}
