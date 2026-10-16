import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const PARTY: FieldDef["pick"] = { endpoint: "/api/core/parties/", permission: "core.view_party", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const WAREHOUSE: FieldDef["ref"] = { endpoint: "/api/inventory/warehouses/", permission: "inventory.view_warehouse", label: (row: Row) => String(row.name || row.code) };

/** A place stock is kept: a store, a quarantine bay, goods in transit, or a customer's material held here. */
export default function WarehouseForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/warehouses/"
      back="/stores/warehouses"
      backLabel="Warehouses"
      newTitle="New warehouse"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "inventory.add_warehouse", change: "inventory.change_warehouse", delete: "inventory.delete_warehouse" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "consignment_vendor", label: "Consignment vendor", kind: "pick", pick: PARTY, hint: "Set when the stock here belongs to a vendor until it is used" },
        { key: "is_quarantine", label: "Quarantine", kind: "bool", initial: false, hint: "Holds goods received but not yet accepted" },
        { key: "is_transit", label: "Transit", kind: "bool", initial: false, hint: "Holds stock that has left one warehouse and not yet arrived at another" },
        { key: "requires_bins", label: "Requires bins", kind: "bool", initial: false, hint: "Every movement in or out must name a bin" },
        { key: "receipt_route", label: "Receipt route", kind: "choice", choices: [["direct", "Straight to stock"], ["input", "Receiving bay, then stock"], ["inspect", "Receiving bay, then inspection, then stock"]], initial: "direct", hint: "How many places goods pass through on the way in" },
        { key: "input_warehouse", label: "Input warehouse", kind: "ref", ref: WAREHOUSE, hint: "The bay goods land in before they are put away" },
        { key: "quality_warehouse", label: "Quality warehouse", kind: "ref", ref: WAREHOUSE, hint: "Where goods wait to be looked at" },
        { key: "allow_negative_stock", label: "Allow negative stock", kind: "bool", initial: false, hint: "Permit shipping more than is on hand (backorders, in-transit stock)" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "held_for", label: "Held for", kind: "pick", pick: PARTY, hint: "Set when the stock here is a customer's, sent to be worked on" },
      ]}
    />
  );
}
