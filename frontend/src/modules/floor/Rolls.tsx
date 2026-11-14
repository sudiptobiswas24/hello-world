import { ListView, type Column } from "../../views/ListView";

interface Roll { id: number; lot_code: string; specification_name: string; machine_code: string; width_mm: string; length_m: string;
  net_weight_kg: string; implied_gsm: string; gsm_deviation_percent: string | null; within_tolerance: boolean | null;
  [key: string]: unknown }

const columns: Column<Roll>[] = [
  { key: "lot_code", label: "Roll", width: "10rem" },
  { key: "specification_name", label: "Fabric" },
  { key: "machine_code", label: "Loom", width: "7rem" },
  { key: "width_mm", label: "Width mm", kind: "quantity", width: "7rem" },
  { key: "length_m", label: "Metres", kind: "quantity", width: "7rem" },
  { key: "net_weight_kg", label: "Kg", kind: "quantity", width: "7rem" },
  { key: "implied_gsm", label: "GSM", kind: "quantity", width: "7rem" },
  { key: "gsm_deviation_percent", label: "Off by %", kind: "quantity", width: "7rem" },
  { key: "within_tolerance", label: "", width: "7rem",
    render: (row) => (row.within_tolerance === null ? "" : row.within_tolerance ? "In" : "Out") },
];

/** Every roll off the looms, and what it actually weighs a square metre against its fabric's. */
export default function Rolls() {
  return (
    <ListView<Roll>
      title="Fabric rolls"
      noun={["fabric roll", "fabric rolls"]}
      endpoint="/api/manufacturing/fabric-rolls/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Roll"
    />
  );
}
