import { quantity } from "../../lib/format";
import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };

const blankOr = (value: unknown, places: number) => (value === null || value === undefined ? "" : quantity(value as string, places));

/**
 * The owner's morning question: what each section made yesterday, what
 * went in the skip, what the meters drew and the kWh a kilogramme. A
 * section counted in a unit nothing converts shows its unit and no
 * kilogrammes, so its kWh a kilo is blank rather than wrong.
 */
export default function DailyProduction() {
  return (
    <ReportView<Row[], Row>
      title="Yesterday's production"
      endpoint="/api/manufacturing/work-centres/daily/"
      params={[{ key: "day", label: "Day", kind: "date", initial: "-1", required: true }]}
      rows={(data) => data}
      columns={[
        { key: "section", label: "Section", render: (row) => `${String(row.code)} · ${String(row.name)}` },
        { key: "made", label: "Made", kind: "quantity", width: "9rem", render: (row) => `${quantity(row.made as string, 0)} ${String(row.unit ?? "")}` },
        { key: "kg", label: "kg", kind: "quantity", width: "8rem", render: (row) => blankOr(row.kg, 0) },
        { key: "scrapped", label: "Scrap", kind: "quantity", width: "8rem", render: (row) => quantity(row.scrapped as string, 0) },
        { key: "waste_percent", label: "Waste %", kind: "quantity", width: "7rem", render: (row) => blankOr(row.waste_percent, 2) },
        { key: "kwh", label: "kWh", kind: "quantity", width: "7rem", render: (row) => blankOr(row.kwh, 0) },
        { key: "idle_kwh", label: "of it idle", kind: "quantity", width: "7rem", render: (row) => blankOr(row.idle_kwh, 0) },
        { key: "kwh_per_kg", label: "kWh a kg", kind: "quantity", width: "8rem", render: (row) => blankOr(row.kwh_per_kg, 3) },
        { key: "unread", label: "", width: "7rem", render: (row) => (row.unread ? <span className="pill pill-warn">meter unread</span> : "") },
      ]}
      empty="No section is set up yet."
    />
  );
}
