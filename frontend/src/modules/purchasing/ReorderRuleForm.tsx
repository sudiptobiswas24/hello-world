import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" }, label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const ITEM: FieldDef["pick"] = { endpoint: "/api/inventory/items/", permission: "inventory.view_item", label: (row: Row) => `${String(row.sku)} · ${String(row.name)}` };
const WAREHOUSE: FieldDef["ref"] = { endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse", label: (row: Row) => String(row.name || row.code) };

/** When to buy more of an item at a warehouse, and how much: read by the reorder report and by planning. */
export default function ReorderRuleForm() {
  return (
    <RecordScreen
      endpoint="/api/purchasing/reorder-rules/"
      back="/purchasing/reorder-rules"
      backLabel="Reorder rules"
      newTitle="New reorder rule"
      heading={(row) => String(row.item_label ?? "") + " · " + String(row.warehouse_name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "purchasing.add_reorderrule", change: "purchasing.change_reorderrule", delete: "purchasing.delete_reorderrule" }}
      fields={[
        { key: "item", label: "Item", kind: "pick", pick: ITEM },
        { key: "warehouse", label: "Warehouse", kind: "ref", ref: WAREHOUSE },
        { key: "minimum", label: "Minimum", kind: "decimal", hint: "Order when projected stock falls to or below this" },
        { key: "target", label: "Target", kind: "decimal", hint: "Order enough to bring projected stock back up to this" },
        { key: "multiple_of", label: "Multiple of", kind: "decimal", hint: "Round the order up to a whole case, pallet or bag size" },
        { key: "minimum_order_quantity", label: "Minimum order quantity", kind: "decimal", hint: "The least anybody will sell, or the least worth making" },
        { key: "maximum_order_quantity", label: "Maximum order quantity", kind: "decimal", hint: "The most that goes into one order — a silo, a mixer or a lorry" },
        { key: "order_period_days", label: "Order period days", kind: "integer", initial: 0, hint: "Order once for this many days of demand instead of once per shortage" },
        { key: "vendor", label: "Vendor", kind: "pick", pick: PARTY, hint: "Buy from this vendor" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
    />
  );
}
