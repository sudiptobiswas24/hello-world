import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const BAG: FieldDef["ref"] = {
  endpoint: "/api/manufacturing/bag-specifications/", permission: "manufacturing.view_bagspecification",
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
const QUOTATION: FieldDef["pick"] = {
  endpoint: "/api/sales/quotations/", permission: "sales.view_quotation", query: { status: "draft" },
  label: (row: Row) => `${String(row.number || "Draft")} · ${String(row.customer_name)}`,
};

/**
 * What a sack costs to make at the day's rates, frozen when it is costed:
 * material, conversion by stage, by-product credit, overhead and margin.
 * Put on a quotation, its price becomes the line's.
 */
export default function CostSheetForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/cost-sheets/"
      back="/making/cost-sheets"
      backLabel="Cost sheets"
      newTitle="Cost a sack"
      heading={(row) => `${String(row.specification_code ?? "")} · ${String(row.costed_on ?? "")}`}
      state={(row) => (row.quotation_line ? { label: "Quoted", tone: "done" } : null)}
      permissions={{ add: "manufacturing.add_costsheet" }}
      fields={[
        { key: "specification", label: "Sack", kind: "ref", ref: BAG, createOnly: true, show: (row) => String(row.specification_code ?? "") },
        { key: "quantity", label: "Quantity", kind: "decimal", places: 2, createOnly: true },
        { key: "costed_on", label: "At the rates of", kind: "date", createOnly: true, hint: "Today, if left empty" },
        { key: "margin_percent", label: "Margin %", kind: "decimal", places: 2, createOnly: true, hint: "The quotation policy's, if left empty" },
        { key: "bag_grams", label: "A sack weighs (g)", readOnly: true, existingOnly: true, kind: "decimal" },
        { key: "material", label: "Material", readOnly: true, existingOnly: true, kind: "money" },
        { key: "conversion", label: "Conversion", readOnly: true, existingOnly: true, kind: "money" },
        { key: "credit", label: "By-product credit", readOnly: true, existingOnly: true, kind: "money" },
        { key: "overhead", label: "Overhead", readOnly: true, existingOnly: true, kind: "money" },
        { key: "cost", label: "Cost", readOnly: true, existingOnly: true, kind: "money" },
        { key: "price", label: "Price", readOnly: true, existingOnly: true, kind: "money" },
        { key: "per_kg", label: "A kg", readOnly: true, existingOnly: true, kind: "money" },
        { key: "order_value", label: "Order value", readOnly: true, existingOnly: true, kind: "money" },
      ]}
      actions={[
        { label: "Put on a quotation", path: "quote", permission: "sales.add_quotationline", primary: true,
          when: (row) => !row.quotation_line, done: "Put on the quotation",
          fields: [{ key: "quotation", label: "Quotation", kind: "pick", pick: QUOTATION,
            hint: "A draft. The line's taxes are set on the quotation" }],
          body: (values) => ({ quotation: values.quotation, taxes: [] }) },
      ]}
      panels={[{
        title: "How it is made up", permission: "manufacturing.view_costsheet", endpoint: "", query: () => ({}),
        rows: (sheet) => ((sheet.lines as Row[]) ?? []).map((line, index) => ({ ...line, id: index })),
        columns: [
          { key: "kind", label: "Part", width: "8rem" },
          { key: "stage", label: "Stage", width: "8rem" },
          { key: "description", label: "What" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
          { key: "rate", label: "Rate", kind: "money", width: "8rem" },
          { key: "amount", label: "Amount", kind: "money", width: "9rem" },
        ],
      }]}
    />
  );
}
