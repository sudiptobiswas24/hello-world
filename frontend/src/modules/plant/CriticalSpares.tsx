import { quantity } from "../../lib/format";
import { ReportView } from "../../views/ReportView";

type Row = Record<string, unknown> & { id: number };

/** The spares no machine runs without, and whether any is on a shelf: what to keep in stock, and how long each lasts. */
export default function CriticalSpares() {
  return (
    <ReportView<Row[], Row>
      title="Critical spares"
      endpoint="/api/manufacturing/machine-positions/critical-spares/"
      params={[]}
      rows={(data) => data.map((row) => ({ ...row, id: row.position_id as number }))}
      columns={[
        { key: "machine", label: "Machine", width: "8rem" },
        { key: "position", label: "Position", width: "12rem" },
        { key: "item", label: "Spare", width: "10rem" },
        { key: "item_name", label: "" },
        { key: "on_hand", label: "On any shelf", kind: "quantity", width: "9rem", render: (row) => quantity(row.on_hand as string, 0) },
        { key: "life_days", label: "Lasts, days", kind: "quantity", width: "8rem", render: (row) => (row.life_days === null ? "" : quantity(row.life_days as string, 1)) },
        { key: "short", label: "", width: "6rem", render: (row) => (row.short ? <span className="pill pill-warn">none</span> : "") },
      ]}
      empty="No position is marked critical yet: mark them on each machine."
    />
  );
}
