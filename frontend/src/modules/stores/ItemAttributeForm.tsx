import { RecordScreen } from "../../views/RecordScreen";

/** Something items vary by, a size or a colour, and the values it takes. */
export default function ItemAttributeForm() {
  return (
    <RecordScreen
      endpoint="/api/inventory/item-attributes/"
      back="/stores/attributes"
      backLabel="Item attributes"
      newTitle="New item attribute"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      state={(row) => (row.is_active === false ? { label: "Inactive", tone: "draft" } : null)}
      permissions={{ add: "inventory.add_itemattribute", change: "inventory.change_itemattribute", delete: "inventory.delete_itemattribute" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "sequence", label: "Sequence", kind: "integer", initial: 100, hint: "Order attributes appear in on a variant's name" },
        { key: "is_active", label: "Active", kind: "bool", initial: true },
      ]}
      panels={[{
        title: "Values", permission: "inventory.view_itemattributevalue", endpoint: "/api/inventory/item-attribute-values/",
        query: (attribute) => ({ attribute: String(attribute.id) }),
        columns: [
          { key: "code", label: "Code", width: "8rem" },
          { key: "name", label: "Name" },
          { key: "sequence", label: "Order", kind: "quantity", width: "6rem" },
          { key: "is_active", label: "", width: "6rem", render: (row) => (row.is_active ? "" : "Retired") },
        ],
        adder: { label: "Add a value", permission: "inventory.add_itemattributevalue", url: () => "/api/inventory/item-attribute-values/",
          fields: [
            { key: "code", label: "Code", hint: "What it adds to a variant's SKU: 60X100" },
            { key: "name", label: "Name" },
            { key: "sequence", label: "Order", kind: "integer", initial: 10 },
          ],
          body: (values, attribute) => ({ ...values, attribute: attribute.id }) },
      }]}
    />
  );
}
