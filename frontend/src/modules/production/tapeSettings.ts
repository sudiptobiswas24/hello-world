import { dateTime } from "../../lib/format";
import type { PanelDef } from "../../views/RecordScreen";
import { MACHINE } from "../floor/refs";

type Row = Record<string, unknown> & { id: number };

/**
 * What the tape line actually ran at on a run, beside the draw ratio the
 * specification asked for. A change mid-run is a new row, never an edit:
 * a strength complaint reads what the line was set to when the batch came
 * off it.
 */
export const TAPE_SETTINGS_PANEL: PanelDef = {
  title: "Tape line settings", permission: "manufacturing.view_taperunsetting",
  endpoint: "/api/manufacturing/tape-run-settings/", query: (order) => ({ work_order: String(order.id) }),
  columns: [
    { key: "recorded_at", label: "At", width: "11rem", render: (row: Row) => dateTime(row.recorded_at as string) },
    { key: "machine_code", label: "Machine", width: "8rem" },
    { key: "draw_ratio", label: "Draw ratio", kind: "quantity", width: "8rem" },
    { key: "quench_temperature_c", label: "Quench °C", kind: "quantity", width: "8rem" },
    { key: "oven_temperature_c", label: "Oven °C", kind: "quantity", width: "8rem" },
    { key: "note", label: "Note" },
  ],
  adder: { label: "Record the settings", permission: "manufacturing.add_taperunsetting",
    when: (order) => order.status === "released" || order.status === "closed",
    url: () => "/api/manufacturing/tape-run-settings/",
    fields: [
      { key: "machine", label: "Machine", kind: "ref", ref: MACHINE, hint: "Empty where the run has one line" },
      { key: "draw_ratio", label: "Draw ratio", kind: "decimal", places: 2, hint: "Godet speeds, out over in: 6.5 for 6.5:1" },
      { key: "quench_temperature_c", label: "Quench bath °C", kind: "decimal", places: 1 },
      { key: "oven_temperature_c", label: "Oven °C", kind: "decimal", places: 1 },
      { key: "note", label: "Note", kind: "text" },
    ],
    body: (values, order) => ({ ...values, work_order: order.id }) },
};
