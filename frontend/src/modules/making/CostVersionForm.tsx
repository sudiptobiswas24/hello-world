import { RecordScreen } from "../../views/RecordScreen";
import { ITEM } from "./refs";

const draft = (row: Record<string, unknown>) => !row.published_on;

/**
 * A set of standard costs from a date. Rolled up from the recipes, or
 * typed for a bought item; published, it revalues the stock at the new
 * standards and cannot be changed again: the next version replaces it.
 */
export default function CostVersionForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/cost-versions/"
      back="/making/cost-versions"
      backLabel="Standard cost versions"
      newTitle="New cost version"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      state={(row) => (row.published_on ? { label: "Published", tone: "done" } : { label: "Draft", tone: "open" })}
      permissions={{ add: "manufacturing.add_costversion", change: "manufacturing.change_costversion",
        delete: "manufacturing.delete_costversion" }}
      editable={draft}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "effective_from", label: "From", kind: "date" },
        { key: "notes", label: "Notes", kind: "textarea" },
        { key: "published_on", label: "Published on", kind: "date", readOnly: true },
      ]}
      actions={[
        { label: "Roll up from the recipes", path: "roll_up", permission: "manufacturing.change_costversion",
          when: draft, done: "Rolled up" },
        { label: "Publish", path: "publish", permission: "manufacturing.change_costversion", when: draft, primary: true,
          done: "Published", fields: [{ key: "on_date", label: "Revalue stock on", kind: "date", hint: "Empty: the version's date" }] },
      ]}
      panels={[{
        title: "Standard costs", permission: "manufacturing.view_standardcost",
        endpoint: "/api/manufacturing/standard-costs/", query: (record) => ({ version: record.id }),
        columns: [
          { key: "item_label", label: "Item" },
          { key: "material", label: "Material", kind: "money", width: "9rem" },
          { key: "conversion", label: "Conversion", kind: "money", width: "9rem" },
          { key: "byproduct_credit", label: "By-product credit", kind: "money", width: "9rem" },
          { key: "total", label: "Standard", kind: "money", width: "9rem" },
          { key: "is_rolled", label: "", width: "6rem", render: (row) => (row.is_rolled ? "Rolled" : "Typed") },
        ],
        adder: { label: "Add a standard", permission: "manufacturing.add_standardcost", when: draft,
          url: () => "/api/manufacturing/standard-costs/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "material", label: "Material", kind: "money" },
            { key: "conversion", label: "Conversion", kind: "money", initial: "0" },
            { key: "byproduct_credit", label: "By-product credit", kind: "money", initial: "0" },
          ],
          body: (values, record) => ({ version: record.id, ...values }) },
        remover: { permission: "manufacturing.delete_standardcost", when: draft,
          url: (row) => `/api/manufacturing/standard-costs/${row.id}/` },
      }]}
    />
  );
}
