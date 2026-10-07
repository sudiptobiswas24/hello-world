import { RecordScreen } from "../../views/RecordScreen";
import { OUTSIDE_STEP, VENDOR, draft, postedState } from "./partyRefs";

type Row = Record<string, unknown> & { id: number };

const issued = (row: Row) => Boolean(row.posted) && !row.voided_at;

/**
 * Material sent to a job worker for an outside step, under a delivery
 * challan. Posted, it is stock at the job worker's until it comes back,
 * and reported on ITC-04; a wrong one is voided, never edited.
 */
export default function JobWorkForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/job-work-challans/"
      links={[{ label: "PDF", href: (row) => `/api/manufacturing/job-work-challans/${String(row.id)}/pdf/` }]}
      back="/stores/job-work"
      backLabel="Job-work challans"
      newTitle="New job-work challan"
      heading={(row) => `${String(row.number || "Challan")} · ${String(row.job_worker_name ?? "")}`}
      state={postedState}
      permissions={{ add: "manufacturing.add_jobworkchallan", change: "manufacturing.change_jobworkchallan",
        delete: "manufacturing.delete_jobworkchallan" }}
      editable={draft}
      fields={[
        { key: "job_worker", label: "Job worker", kind: "pick", pick: VENDOR, createOnly: true,
          show: (row) => String(row.job_worker_name) },
        { key: "challan_date", label: "Date", kind: "date" },
        { key: "vehicle", label: "Vehicle" },
        { key: "notes", label: "Notes", wide: true },
        { key: "job_worker_gstin", label: "Their GSTIN", readOnly: true },
        { key: "job_worker_state", label: "Their state", readOnly: true },
      ]}
      actions={[
        { label: "Post", path: "post", permission: "manufacturing.change_jobworkchallan", when: draft, primary: true, done: "Posted" },
        { label: "Void", path: "void", permission: "manufacturing.change_jobworkchallan", danger: true,
          when: (row) => Boolean(row.posted) && !row.voided_at, done: "Voided" },
      ]}
      panels={[{
        title: "Goods sent", permission: "manufacturing.view_jobworkline", endpoint: "", query: () => ({}),
        rows: (record) => (record.lines as Row[]) ?? [],
        columns: [
          { key: "operation_label", label: "For step" },
          { key: "description", label: "Goods" },
          { key: "hsn_code", label: "HSN", width: "7rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
          { key: "value", label: "Value", kind: "money", width: "9rem" },
        ],
        adder: { label: "Add goods", permission: "manufacturing.add_jobworkline", when: draft,
          url: () => "/api/manufacturing/job-work-lines/",
          fields: [
            { key: "operation", label: "For step", kind: "pick", pick: OUTSIDE_STEP },
            { key: "description", label: "Goods" },
            { key: "hsn_code", label: "HSN" },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "value", label: "Value", kind: "money" },
            { key: "tax_rate", label: "GST rate %", kind: "decimal", places: 2 },
            { key: "is_capital_goods", label: "Capital goods", kind: "bool" },
          ],
          body: (values, record) => ({ challan: record.id, ...values }) },
        remover: { permission: "manufacturing.delete_jobworkline", when: draft,
          url: (row) => `/api/manufacturing/job-work-lines/${row.id}/` },
      }, {
        // Lost or damaged at the job worker: it will not come back, and
        // ITC-04 reports it as such. Recorded against the line it went out
        // on, voided rather than edited.
        title: "Lost at the job worker", permission: "manufacturing.view_jobworkloss",
        endpoint: "/api/manufacturing/job-work-losses/", query: (record) => ({ line__challan: record.id }),
        columns: [
          { key: "line_description", label: "Goods" },
          { key: "loss_date", label: "Date", kind: "date", width: "8rem" },
          { key: "quantity", label: "Quantity", kind: "quantity", width: "8rem" },
          { key: "note", label: "Note" },
          { key: "voided_at", label: "", width: "6rem", render: (row) => (row.voided_at ? "Voided" : "") },
        ],
        adder: { label: "Record a loss", permission: "manufacturing.add_jobworkloss", when: issued,
          url: () => "/api/manufacturing/job-work-losses/",
          fields: (record) => [
            { key: "line", label: "Goods", kind: "choice",
              choices: ((record.lines as Row[]) ?? []).map((line): [string, string] => [String(line.id), String(line.description)]) },
            { key: "loss_date", label: "Date", kind: "date" },
            { key: "quantity", label: "Quantity", kind: "decimal" },
            { key: "note", label: "Note", wide: true },
          ],
          body: (values) => values },
        rowAction: { label: "Void", permission: "manufacturing.change_jobworkloss", done: "Voided",
          when: (row) => !row.voided_at, url: (row) => `/api/manufacturing/job-work-losses/${row.id}/void/`,
          confirm: "Void this loss? The goods count as still at the job worker again." },
      }]}
    />
  );
}
