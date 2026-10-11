import { date } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Inspection { id: number; number: string; inspected_on: string; lot_code: string; item_label: string; plan_name: string; result: string; disposition: string; posted: boolean; voided_at: string | null; [key: string]: unknown }

const columns: Column<Inspection>[] = [
  { key: "number", label: "Number", width: "10rem", sort: "number" },
  { key: "inspected_on", label: "On", width: "8rem", sort: "inspected_on", render: (row) => date(row.inspected_on) },
  { key: "lot_code", label: "Batch", width: "10rem" },
  { key: "item_label", label: "Item" },
  { key: "plan_name", label: "Plan" },
  { key: "result", label: "Result", width: "7rem", kind: "status" },
  { key: "disposition", label: "Decided", width: "9rem", kind: "status" },
];

/** Batches tested against their plans, and what was decided about them. */
export default function Inspections() {
  return (
    <ListView<Inspection>
      title="Inspections"
      noun={["inspection", "inspections"]}
      endpoint="/api/quality/inspections/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/inspections/${row.id}`}
      searchHint="Number, batch, item"
      facets={[{ label: "Draft", params: { posted: "false" } }, { label: "Failed", params: { result: "fail" } }]}
      create={{ href: "/quality/inspections/new", permission: "quality.add_inspection" }}
    />
  );
}
