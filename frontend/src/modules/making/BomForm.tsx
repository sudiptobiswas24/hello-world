import { RecordScreen } from "../../views/RecordScreen";
import { BYPRODUCT_VALUATIONS, ITEM, ROUTING, UOM } from "./refs";

type Row = Record<string, unknown> & { id: number };

/**
 * One recipe: what a batch of the item takes, what else comes off it,
 * and the routes it may be made by. A run already made against it keeps
 * what it consumed; changing the recipe changes the next run.
 */
export default function BomForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/boms/"
      back="/making/boms"
      backLabel="Bills of materials"
      newTitle="New bill of materials"
      heading={(row) => `${String(row.item_label)}, version ${String(row.version)}`}
      state={(row) => (!row.is_active ? { label: "Inactive", tone: "draft" } : row.is_default ? { label: "Default", tone: "done" } : null)}
      permissions={{ add: "manufacturing.add_billofmaterials", change: "manufacturing.change_billofmaterials",
        delete: "manufacturing.delete_billofmaterials" }}
      fields={[
        { key: "item", label: "Makes", kind: "pick", pick: ITEM, createOnly: true, show: (row) => String(row.item_label) },
        { key: "version", label: "Version", kind: "integer", initial: 1 },
        { key: "name", label: "Name" },
        { key: "quantity_produced", label: "A batch makes", kind: "decimal" },
        { key: "uom", label: "Unit", kind: "ref", ref: UOM },
        { key: "routing", label: "Routing", kind: "ref", ref: ROUTING },
        { key: "expected_reject_percent", label: "Expected rejects %", kind: "decimal", places: 3, initial: "0" },
        { key: "valid_from", label: "From", kind: "date", hint: "Empty: from the start" },
        { key: "valid_to", label: "To", kind: "date", hint: "Empty: open-ended" },
        { key: "is_default", label: "The default recipe", kind: "bool", initial: true },
        { key: "is_phantom", label: "Phantom (made and used at once)", kind: "bool" },
        { key: "is_rework", label: "A rework recipe", kind: "bool" },
        { key: "backflush", label: "Consume inputs when output is booked", kind: "bool" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
      panels={[
        {
          title: "Inputs", permission: "manufacturing.view_bomcomponent", endpoint: "", query: () => ({}),
          rows: (record) => (record.components as Row[]) ?? [],
          columns: [
            { key: "item_label", label: "Item" },
            { key: "quantity", label: "Per batch", kind: "quantity", width: "9rem" },
            { key: "uom_code", label: "", width: "4rem" },
            { key: "waste_percent", label: "Waste %", kind: "quantity", width: "7rem" },
            { key: "gross_quantity", label: "With waste", kind: "quantity", width: "9rem" },
          ],
          adder: { label: "Add an input", permission: "manufacturing.add_bomcomponent",
            url: () => "/api/manufacturing/bom-components/",
            fields: [
              { key: "item", label: "Item", kind: "pick", pick: ITEM },
              { key: "quantity", label: "Per batch", kind: "decimal", places: 6 },
              { key: "uom", label: "Unit", kind: "ref", ref: UOM },
              { key: "waste_percent", label: "Waste %", kind: "decimal", places: 3, initial: "0" },
            ],
            body: (values, record) => ({ bom: record.id, ...values }) },
          remover: { permission: "manufacturing.delete_bomcomponent", url: (row) => `/api/manufacturing/bom-components/${row.id}/` },
        },
        {
          // What may go in instead of an input when it is short, in the
          // order they are tried.
          title: "Substitutes", permission: "manufacturing.view_bomsubstitute",
          endpoint: "/api/manufacturing/bom-substitutes/", query: (record) => ({ component__bom: String(record.id) }),
          columns: [
            { key: "instead_of", label: "Instead of" },
            { key: "item_label", label: "Use" },
            { key: "quantity_per", label: "For each", kind: "quantity", width: "8rem" },
            { key: "priority", label: "Tried", width: "5rem" },
          ],
          adder: { label: "Add a substitute", permission: "manufacturing.add_bomsubstitute",
            url: () => "/api/manufacturing/bom-substitutes/",
            fields: (record) => [
              { key: "component", label: "Instead of", kind: "choice",
                choices: ((record.components as Row[]) ?? []).map((input): [string, string] => [String(input.id), String(input.item_label)]) },
              { key: "item", label: "Use", kind: "pick", pick: ITEM },
              { key: "quantity_per", label: "For each", kind: "decimal", places: 6, initial: "1",
                hint: "How much of it replaces one of the input" },
              { key: "priority", label: "Tried", kind: "integer", initial: 1 },
            ],
            body: (values) => values },
          remover: { permission: "manufacturing.delete_bomsubstitute", url: (row) => `/api/manufacturing/bom-substitutes/${row.id}/` },
        },
        {
          title: "Also comes off it", permission: "manufacturing.view_bombyproduct", endpoint: "", query: () => ({}),
          rows: (record) => (record.byproducts as Row[]) ?? [],
          columns: [
            { key: "item_label", label: "Item" },
            { key: "quantity", label: "Per batch", kind: "quantity", width: "9rem" },
            { key: "uom_code", label: "", width: "4rem" },
            { key: "valuation", label: "Valued", width: "14rem",
              render: (row) => BYPRODUCT_VALUATIONS.find(([key]) => key === row.valuation)?.[1] ?? String(row.valuation) },
          ],
          adder: { label: "Add a by-product", permission: "manufacturing.add_bombyproduct",
            url: () => "/api/manufacturing/bom-byproducts/",
            fields: [
              { key: "item", label: "Item", kind: "pick", pick: ITEM },
              { key: "quantity", label: "Per batch", kind: "decimal", places: 6 },
              { key: "uom", label: "Unit", kind: "ref", ref: UOM },
              { key: "valuation", label: "Valued", kind: "choice", choices: BYPRODUCT_VALUATIONS, initial: "standard" },
              { key: "cost_share_percent", label: "Share of the run's cost %", kind: "decimal", places: 3 },
            ],
            body: (values, record) => ({ bom: record.id, ...values }) },
          remover: { permission: "manufacturing.delete_bombyproduct", url: (row) => `/api/manufacturing/bom-byproducts/${row.id}/` },
        },
        {
          title: "Other routes", permission: "manufacturing.view_alternaterouting",
          endpoint: "/api/manufacturing/alternate-routings/", query: (record) => ({ bom: record.id }),
          columns: [
            { key: "priority", label: "Tried", width: "6rem" },
            { key: "routing_name", label: "Routing" },
            { key: "notes", label: "Notes" },
          ],
          adder: { label: "Add a route", permission: "manufacturing.add_alternaterouting",
            url: () => "/api/manufacturing/alternate-routings/",
            fields: [
              { key: "routing", label: "Routing", kind: "ref", ref: ROUTING },
              { key: "priority", label: "Tried", kind: "integer", hint: "1 is tried first after the recipe's own" },
              { key: "notes", label: "Notes" },
            ],
            body: (values, record) => ({ bom: record.id, ...values }) },
          remover: { permission: "manufacturing.delete_alternaterouting", url: (row) => `/api/manufacturing/alternate-routings/${row.id}/` },
        },
      ]}
    />
  );
}
