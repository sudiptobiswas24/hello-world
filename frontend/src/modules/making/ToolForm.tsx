import { RecordScreen } from "../../views/RecordScreen";
import { TOOL_KINDS, TOOL_STATUSES, UOM, WORK_CENTRE } from "./refs";

/** A cylinder, die, reed or screen: its life, and what the floor's bookings have used of it. */
export default function ToolForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/tools/"
      back="/making/tools"
      backLabel="Tools"
      newTitle="New tool"
      heading={(row) => `${String(row.code)} · ${String(row.name)}`}
      state={(row) => (row.is_worn ? { label: "Worn out", tone: "warn" } : null)}
      permissions={{ add: "manufacturing.add_tool", change: "manufacturing.change_tool", delete: "manufacturing.delete_tool" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
        { key: "kind", label: "Kind", kind: "choice", choices: TOOL_KINDS },
        { key: "work_centre", label: "Bank", kind: "ref", ref: WORK_CENTRE },
        { key: "life_limit", label: "Life", kind: "decimal", hint: "Empty: it does not wear out by use" },
        { key: "life_uom", label: "Life in", kind: "ref", ref: UOM },
        { key: "status", label: "Status", kind: "choice", choices: TOOL_STATUSES, initial: "available" },
        { key: "acquired_on", label: "Acquired", kind: "date" },
        { key: "expected_on", label: "Expected back or in", kind: "date" },
        { key: "used", label: "Used", readOnly: true },
        { key: "remaining", label: "Left", readOnly: true },
        { key: "notes", label: "Notes", kind: "textarea" },
      ]}
      panels={[{
        title: "Used on", permission: "manufacturing.view_toolusage",
        endpoint: "/api/manufacturing/tool-usage/", query: (record) => ({ tool: record.id }),
        columns: [
          { key: "entry", label: "Production entry", width: "12rem" },
          { key: "quantity", label: "Used", kind: "quantity" },
        ],
      }]}
    />
  );
}
