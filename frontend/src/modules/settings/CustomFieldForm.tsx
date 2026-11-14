import { RecordScreen } from "../../views/RecordScreen";

const KINDS: [string, string][] = [
  ["core.party", "Customers and vendors"], ["inventory.item", "Items"], ["hr.employee", "Employees"],
  ["assets.fixedasset", "Fixed assets"], ["manufacturing.machine", "Machines"], ["sales.salesorder", "Sales orders"],
  ["sales.invoice", "Invoices and credit notes"], ["purchasing.purchaseorder", "Purchase orders"],
  ["purchasing.bill", "Bills and debit notes"],
];
const TYPES: [string, string][] = [
  ["text", "Text"], ["number", "Number"], ["date", "Date"], ["yes_no", "Yes or no"], ["choice", "One of a list"],
];

/**
 * One column a keeper adds to a kind of record. Its key and what it holds
 * are fixed once made (every record stores the value under them); a field
 * no longer wanted is switched off, which hides it and keeps the values.
 */
export default function CustomFieldForm() {
  return (
    <RecordScreen
      endpoint="/api/core/custom-fields/"
      back="/settings/custom-fields"
      backLabel="Custom fields"
      newTitle="New custom field"
      heading={(row) => `${String(row.kind_label ?? "")} · ${String(row.label ?? "")}`}
      state={(row) => (row.is_active ? null : { label: "off", tone: "cancelled" })}
      permissions={{ add: "core.add_customfield", change: "core.change_customfield", delete: "core.delete_customfield" }}
      fields={[
        { key: "kind", label: "On", kind: "choice", choices: KINDS, createOnly: true },
        { key: "label", label: "Field", hint: "As the screen shows it: Credit rating" },
        { key: "key", label: "Key", createOnly: true, hint: "How it is stored and exported: credit_rating. Never changes." },
        { key: "field_type", label: "Holds", kind: "choice", choices: TYPES, createOnly: true },
        { key: "choices", label: "The choices", kind: "textarea", hint: "One a line, for a field that is one of a list" },
        { key: "required", label: "Required when saved from the office", kind: "bool" },
        { key: "hint", label: "Hint under the box" },
        { key: "position", label: "Position", kind: "integer", initial: 10, hint: "Lower comes first" },
        { key: "is_active", label: "In use", kind: "bool", initial: true },
      ]}
    />
  );
}
