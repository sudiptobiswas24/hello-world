import { RecordScreen, type FieldDef, type PanelDef } from "../../views/RecordScreen";
import { ACCOUNT } from "../accounts/refs";
import { ITEM } from "./refs";

type Row = Record<string, unknown>;

const REASON: FieldDef["ref"] = { endpoint: "/api/inventory/adjustment-reasons/", permission: "inventory.view_adjustmentreason", label: (row: Row) => String(row.name || row.code) };
/** What a bale of this sack takes from stores when it is pressed: the cover, the straps, the label. */
const PACKING_PANEL: PanelDef = {
  title: "A bale of it takes", permission: "manufacturing.view_packingline",
  endpoint: "/api/manufacturing/packing-lines/", query: (item) => ({ item: String(item.id) }),
  columns: [
    { key: "packing_item_sku", label: "Material", width: "10rem" },
    { key: "packing_item_name", label: "" },
    { key: "quantity", label: "A bale", kind: "quantity", width: "8rem" },
    { key: "uom", label: "", width: "5rem" },
  ],
  adder: { label: "Add packing", permission: "manufacturing.add_packingline", url: () => "/api/manufacturing/packing-lines/",
    fields: [
      { key: "packing_item", label: "Material", kind: "pick", pick: ITEM },
      { key: "quantity", label: "A bale takes", kind: "decimal", places: 4 },
    ],
    body: (values, item) => ({ item: item.id, packing_item: values.packing_item, quantity: values.quantity }) },
  remover: { permission: "manufacturing.delete_packingline", url: (row) => `/api/manufacturing/packing-lines/${row.id}/` },
};
const UNITOFMEASURE: FieldDef["ref"] = { endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure", label: (row: Row) => String(row.code) };

/** Something bought, made, kept or sold: how it is counted, tracked and valued, and where its value posts. */
export default function ItemForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/items/"
      back="/stores/items"
      backLabel="Items"
      newTitle="New item"
      heading={(row) => String(row.sku ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "inventory.add_item", change: "inventory.change_item", delete: "inventory.delete_item" }}
      fields={[
        { key: "sku", label: "SKU" },
        { key: "name", label: "Name" },
        { key: "description", label: "Description", kind: "textarea" },
        { key: "item_type", label: "Item type", kind: "choice", choices: [["goods", "Goods"], ["service", "Service"]], initial: "goods" },
        { key: "stock_class", label: "Stock class", kind: "choice", hint: "How the bank's stock statement groups it; blank shows as unclassified",
          choices: [["", "Unclassified"], ["raw_material", "Raw material"], ["packing", "Packing material"], ["consumable", "Stores and spares"], ["semi_finished", "Semi-finished"], ["finished", "Finished goods"]], initial: "" },
        { key: "uom", label: "Counted in", kind: "ref", ref: UNITOFMEASURE, hint: "Fixed once stock has moved" },
        { key: "track_inventory", label: "Track inventory", kind: "bool", initial: true, hint: "Services and non-stocked items should be False so they never affect stock levels" },
        { key: "tracking", label: "Tracking", kind: "choice", choices: [["none", "Not tracked"], ["lot", "By lot or batch"], ["serial", "By serial number"]], initial: "none", createOnly: true, hint: "Set when the item is made: stock already held would name no batch" },
        { key: "costing_method", label: "Costing method", kind: "choice", choices: [["average", "Weighted average"], ["fifo", "First in, first out"], ["standard", "Standard cost"], ["specific", "Specific identification"]], initial: "average", createOnly: true, hint: "Set when the item is made: stock already held was valued another way" },
        { key: "standard_cost", label: "Standard cost", readOnly: true },
        { key: "sale_price", label: "Sale price", kind: "decimal", places: 2, hint: "Default list price, used when no price list covers this item" },
        { key: "inventory_account", label: "Inventory account", kind: "pick", pick: ACCOUNT, hint: "Asset account holding this item's stock value" },
        { key: "cogs_account", label: "COGS account", kind: "pick", pick: ACCOUNT, hint: "Expense account charged when this item is sold" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "hsn_code", label: "HSN code", hint: "The tariff code tax law classifies this under — HSN for goods, SAC for a service" },
      ]}
      actions={[
        // A new standard revalues the stock on hand: a posting, so it is
        // asked of whoever posts journals, with the reason it books to.
        { label: "Set the standard cost", path: "set_standard_cost", permission: "accounting.post_journalentry",
          when: (row) => row.costing_method === "standard", done: "Standard cost set",
          fields: [
            { key: "standard_cost", label: "Standard cost", kind: "decimal" },
            { key: "on_date", label: "From", kind: "date" },
            { key: "reason", label: "Reason", kind: "ref", ref: REASON },
          ] },
      ]}
      panels={[PACKING_PANEL, {
        // Other units it is bought or sold in, and how many of its own
        // unit each holds: a bale of 500 sacks, a 25 kg bag.
        title: "Other units", permission: "inventory.view_itemunit", endpoint: "/api/inventory/item-units/",
        query: (item) => ({ item: String(item.id) }),
        columns: [
          { key: "uom_code", label: "Unit", width: "8rem" },
          { key: "factor", label: "Holds", kind: "quantity" },
        ],
        adder: { label: "Add a unit", permission: "inventory.add_itemunit", url: () => "/api/inventory/item-units/",
          fields: [
            { key: "uom", label: "Unit", kind: "ref", ref: UNITOFMEASURE },
            { key: "factor", label: "Holds", kind: "decimal", hint: "How many of the item's own unit one of these holds" },
          ],
          body: (values, item) => ({ ...values, item: item.id }) },
        remover: { permission: "inventory.delete_itemunit", url: (row) => `/api/inventory/item-units/${String(row.id)}/` },
      }]}
    />
  );
}
