import { RecordScreen } from "../../views/RecordScreen";
import { DIRECTIONS, ITEM, LOT, RUN, UOM, VOID_FIELDS, WAREHOUSE, draft, postedState } from "./refs";

/**
 * Material from the store to a run, or back. Posted, it moves the stock
 * at what it cost on the shelf into the run's work in progress; a wrong
 * one is voided, never edited.
 */
export default function IssueForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/material-issues/"
      back="/production/issues"
      backLabel="Material issues"
      newTitle="New material issue"
      heading={(row) => `${String(row.number || "Issue")} · ${String(row.work_order_number ?? "")}`}
      state={postedState}
      permissions={{ add: "manufacturing.add_materialissue", change: "manufacturing.change_materialissue",
        delete: "manufacturing.delete_materialissue" }}
      editable={draft}
      fields={[
        { key: "work_order", label: "Run", kind: "pick", pick: RUN, createOnly: true,
          show: (row) => `${String(row.work_order_number)} · ${String(row.makes)}` },
        { key: "direction", label: "Which way", kind: "choice", choices: DIRECTIONS, initial: "issue", createOnly: true },
        { key: "issue_date", label: "Date", kind: "date" },
        { key: "warehouse", label: "Store", kind: "ref", ref: WAREHOUSE },
        { key: "memo", label: "Memo", wide: true },
        { key: "posted_value", label: "Value", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "manufacturing.change_materialissue", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "manufacturing.change_materialissue", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided", fields: VOID_FIELDS },
      ]}
      panels={[{
        title: "Material", permission: "manufacturing.view_materialissueline", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as (Record<string, unknown> & { id: number })[]) ?? [],
        columns: [
          { key: "item_label", label: "Item" },
          { key: "lot_code", label: "Batch", width: "9rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem" },
          { key: "uom_code", label: "", width: "4rem" },
          { key: "unit_cost", label: "At", kind: "money", width: "8rem" },
        ],
        adder: { label: "Add material", permission: "manufacturing.add_materialissueline", when: draft,
          url: () => "/api/manufacturing/material-issue-lines/",
          fields: [
            { key: "item", label: "Item", kind: "pick", pick: ITEM },
            { key: "lot", label: "Batch", kind: "pick", pick: LOT, hint: "For a batch-tracked item" },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "uom", label: "Unit", kind: "ref", ref: UOM },
          ],
          body: (values, record) => ({ issue: record.id, ...values }) },
        remover: { permission: "manufacturing.delete_materialissueline", when: draft,
          url: (row) => `/api/manufacturing/material-issue-lines/${row.id}/` },
      }]}
    />
  );
}
