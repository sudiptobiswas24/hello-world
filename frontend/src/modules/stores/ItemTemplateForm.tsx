import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const UNITOFMEASURE: FieldDef["ref"] = { endpoint: "/api/core/units-of-measure/", permission: "core.view_unitofmeasure", label: (row: Row) => String(row.code) };

/** One item in many variants (sizes, colours): each variant is an item of its own, made from here. */
export default function ItemTemplateForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/item-templates/"
      back="/stores/item-templates"
      backLabel="Item templates"
      newTitle="New item template"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "inventory.add_itemtemplate", change: "inventory.change_itemtemplate", delete: "inventory.delete_itemtemplate" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "description", label: "Description", kind: "textarea" },
        { key: "uom", label: "Uom", kind: "ref", ref: UNITOFMEASURE },
        { key: "item_type", label: "Item type", initial: "goods" },
        { key: "track_inventory", label: "Track inventory", kind: "bool", initial: true },
        { key: "tracking", label: "Tracking", initial: "none" },
        { key: "costing_method", label: "Costing method", initial: "average" },
        { key: "inventory_account", label: "Inventory account", kind: "pick", pick: ACCOUNT },
        { key: "cogs_account", label: "COGS account", kind: "pick", pick: ACCOUNT },
        { key: "sale_price", label: "Sale price", kind: "decimal", places: 2, hint: "Starting point for a variant's own price" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "variant_count", label: "Variant count", readOnly: true },
      ]}
      actions={[
        { label: "Make its variants", path: "generate-variants", permission: "inventory.add_item", primary: true,
          when: (row) => Boolean(row.is_active),
          done: "Variants made" },
        { label: "Retire it and its variants", path: "deactivate", permission: "inventory.change_itemtemplate", danger: true,
          when: (row) => Boolean(row.is_active), done: "Retired" },
      ]}
    />
  );
}
