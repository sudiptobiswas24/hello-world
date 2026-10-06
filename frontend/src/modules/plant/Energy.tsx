import { sum } from "../../lib/decimal";
import { money, quantity } from "../../lib/format";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row {
  meter: number; code: string; serves: string; kwh: string; run_kwh: string; idle_kwh: string;
  cost: string | null; readings: number; days_unread: number; [key: string]: unknown;
}

const PARAMS: ParamDef[] = [
  { key: "start", label: "From", kind: "date", initial: "month-start" },
  { key: "end", label: "To", kind: "date", initial: "today" },
];

/**
 * What each meter recorded, how much went to runs and how much was drawn
 * with nothing booked, and the days nobody read it.
 */
export default function Energy() {
  return (
    <ReportView<Row[], Row>
      title="Electricity"
      endpoint="/api/manufacturing/energy-meters/summary/"
      params={PARAMS}
      rows={(data) => data}
      columns={[
        { key: "code", label: "Meter", width: "9rem" },
        { key: "serves", label: "On" },
        { key: "kwh", label: "kWh", kind: "quantity", render: (row) => quantity(row.kwh, 1) },
        { key: "run_kwh", label: "To runs", kind: "quantity", render: (row) => quantity(row.run_kwh, 1) },
        { key: "idle_kwh", label: "Idle", kind: "quantity", render: (row) => quantity(row.idle_kwh, 1) },
        { key: "max_demand_kva", label: "Max kVA", kind: "quantity", render: (row) => (row.max_demand_kva ? quantity(row.max_demand_kva as string, 1) : "") },
        { key: "power_factor", label: "PF", kind: "quantity", render: (row) => (row.power_factor ? quantity(row.power_factor as string, 3) : "") },
        { key: "cost", label: "Cost", kind: "money", render: (row) => (row.cost === null ? "No tariff" : money(row.cost)) },
        { key: "days_unread", label: "Days unread", kind: "quantity", render: (row) => String(row.days_unread) },
      ]}
      foot={(rows) => ["Total", null, quantity(sum(rows.map((row) => row.kwh)), 1), null, null, null, null]}
      empty="No meter was read in these days."
    />
  );
}
