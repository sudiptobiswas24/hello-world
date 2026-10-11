import { RecordScreen } from "../../views/RecordScreen";
import { ITEM, LOT, MACHINE, RUN, SCRAP_REASON, UOM, VOID_FIELDS, WAREHOUSE, draft, postedState } from "./refs";

type Row = Record<string, unknown> & { id: number };

/**
 * What came off a run: the good quantity into stock at the run's cost,
 * the scrap with why, and anything else that came off it. Most arrive
 * from the stations; this is for the office to review, correct by
 * voiding, and enter what a station did not.
 */
export default function EntryForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/production-entries/"
      back="/production/output"
      backLabel="Output"
      newTitle="New production entry"
      heading={(row) => `${String(row.number || "Entry")} · ${String(row.work_order_number ?? "")}`}
      state={postedState}
      permissions={{ add: "manufacturing.add_productionentry", change: "manufacturing.change_productionentry",
        delete: "manufacturing.delete_productionentry" }}
      editable={draft}
      fields={[
        { key: "work_order", label: "Run", kind: "pick", pick: RUN, createOnly: true,
          show: (row) => `${String(row.work_order_number)} · ${String(row.makes)}` },
        { key: "entry_date", label: "Date", kind: "date" },
        { key: "warehouse", label: "Into", kind: "ref", ref: WAREHOUSE },
        { key: "quantity_produced", label: "Good", kind: "decimal" },
        { key: "quantity_scrapped", label: "Scrap", kind: "decimal", initial: "0" },
        { key: "uom", label: "Unit", kind: "ref", ref: UOM },
        { key: "lot", label: "Batch", kind: "pick", pick: LOT, hint: "For a batch-tracked item", show: (row) => String(row.lot_code || "—") },
        { key: "machine", label: "Machine", kind: "ref", ref: MACHINE },
        { key: "memo", label: "Memo", wide: true },
        { key: "unit_cost", label: "Cost a unit", readOnly: true },
        { key: "posted_value", label: "Value", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "manufacturing.change_productionentry", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "manufacturing.change_productionentry", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided", fields: VOID_FIELDS },
      ]}
      panels={[
        {
          title: "Scrap, and why", permission: "manufacturing.view_productionscrap",
          endpoint: "/api/manufacturing/production-scrap/", query: (record) => ({ entry: record.id }),
          columns: [
            { key: "reason_name", label: "Why" },
            { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          ],
          adder: { label: "Say why", permission: "manufacturing.add_productionscrap", when: draft,
            url: () => "/api/manufacturing/production-scrap/",
            fields: [
              { key: "reason", label: "Why", kind: "ref", ref: SCRAP_REASON },
              { key: "quantity", label: "Quantity", kind: "decimal" },
            ],
            body: (values, record) => ({ entry: record.id, ...values }) },
          remover: { permission: "manufacturing.delete_productionscrap", when: draft,
            url: (row) => `/api/manufacturing/production-scrap/${row.id}/` },
        },
        {
          title: "Also came off it", permission: "manufacturing.view_productionbyproduct", endpoint: "", query: () => ({}),
          rows: (record) => (record.byproducts as Row[]) ?? [],
          columns: [
            { key: "item_label", label: "Item" },
            { key: "lot_code", label: "Batch", width: "9rem" },
            { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
            { key: "uom_code", label: "", width: "4rem" },
          ],
          adder: { label: "Add a by-product", permission: "manufacturing.add_productionbyproduct", when: draft,
            url: () => "/api/manufacturing/production-byproducts/",
            fields: [
              { key: "item", label: "Item", kind: "pick", pick: ITEM },
              { key: "quantity", label: "Quantity", kind: "decimal" },
              { key: "uom", label: "Unit", kind: "ref", ref: UOM },
              { key: "lot", label: "Batch", kind: "pick", pick: LOT },
            ],
            body: (values, record) => ({ entry: record.id, ...values }) },
          remover: { permission: "manufacturing.delete_productionbyproduct", when: draft,
            url: (row) => `/api/manufacturing/production-byproducts/${row.id}/` },
        },
      ]}
    />
  );
}
