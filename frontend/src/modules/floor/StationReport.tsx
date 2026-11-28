import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Loom { loom: string; rolls: number; from_weight: string; declared: string; variance_percent: string; tolerance_percent: string; over: boolean; [key: string]: unknown }
interface Exception { kind: string; title: string; [key: string]: unknown }
interface Report { station: string; shift_date: string; rolls: number; manual_percent: string; looms: Loom[]; exceptions: Exception[] }

const PARAMS: ParamDef[] = [
  { key: "station", label: "Station", kind: "ref", required: true,
    ref: { endpoint: "/api/manufacturing/station-reports/", label: (row) => String(row.name || row.code), value: (row) => String(row.code),
      permission: "manufacturing.view_loomstation" } },
  { key: "date", label: "Shift day", kind: "date", initial: "-1" },
];

/**
 * The 8 AM report for whoever manages a station: yesterday's rolls by
 * loom, what the scale weighed against what was declared, and every
 * exception the floor left behind.
 */
export default function StationReport() {
  const columns: Column<Loom>[] = [
    { key: "loom", label: "Loom", width: "8rem" },
    { key: "rolls", label: "Rolls", kind: "quantity", width: "6rem" },
    { key: "from_weight", label: "From the scale", kind: "quantity", width: "9rem" },
    { key: "declared", label: "Declared", kind: "quantity", width: "9rem" },
    { key: "variance_percent", label: "Off by %", kind: "quantity", width: "7rem" },
    { key: "over", label: "", width: "7rem", render: (row) => (row.over ? "Past tolerance" : "") },
  ];
  return (
    <ReportView<Report, Loom>
      title="Station morning report"
      endpoint={(values) => (values.station ? `/api/manufacturing/station-reports/${values.station}/` : null)}
      params={PARAMS}
      send={(values) => (values.date ? { date: values.date } : {})}
      rows={(data) => data.looms}
      columns={columns}
      above={(data) => (
        <p className="note" role="note">
          {data.rolls} rolls on {data.shift_date}; {data.manual_percent}% typed rather than weighed.
          {data.exceptions.length ? ` ${data.exceptions.length} to look at: ${data.exceptions.map((row) => row.title).join("; ")}.` : ""}
        </p>
      )}
      empty="No rolls that day."
    />
  );
}
