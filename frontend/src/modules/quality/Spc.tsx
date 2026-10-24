import { ReportView, type ParamDef } from "../../views/ReportView";

interface Point { inspected_on: string; mean?: string; range?: string; ucl?: string; lcl?: string; signals?: string[]; [key: string]: unknown }
interface Data { centre: string | null; sigma: string | null; points: Point[]; capability: Record<string, string | null> | null; [key: string]: unknown }

const PARAMS: ParamDef[] = [
  { key: "item", label: "Item", kind: "ref", required: true,
    ref: { endpoint: "/api/quality/plans/", permission: "quality.view_inspectionplan",
      label: (row) => String(row.item_label ?? row.name), value: (row) => String(row.item) } },
  { key: "characteristic", label: "Characteristic", kind: "ref", required: true,
    ref: { endpoint: "/api/quality/characteristics/", permission: "quality.view_characteristic",
      label: (row) => String(row.name), value: (row) => String(row.code) } },
  { key: "start", label: "From", kind: "date", initial: "-90" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/** Whether a process is steady: each subgroup against its control limits, with the signals that say it moved. */
export default function Spc() {
  return (
    <ReportView<Data, Point>
      title="Control chart"
      endpoint="/api/quality/spc/"
      params={PARAMS}
      rows={(data) => data.points}
      above={(data) => (
        <p className="note" role="note">
          Centre {data.centre ?? "—"}, sigma {data.sigma ?? "—"}
          {data.capability ? `; Cp ${data.capability.cp ?? "—"}, Cpk ${data.capability.cpk ?? "—"}` : ""}
        </p>
      )}
      columns={[
        { key: "inspected_on", label: "On", kind: "date", width: "8rem" },
        { key: "mean", label: "Mean", kind: "quantity" },
        { key: "range", label: "Range", kind: "quantity" },
        { key: "lcl", label: "Lower limit", kind: "quantity" },
        { key: "ucl", label: "Upper limit", kind: "quantity" },
        { key: "signals", label: "Signals", render: (row) => (row.signals ?? []).join("; ") },
      ]}
      waiting="Choose an item and a characteristic."
      empty="Nothing was inspected in these days."
    />
  );
}
