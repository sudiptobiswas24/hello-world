import { ListView, type Column } from "../../views/ListView";
import { TOOL_KINDS, TOOL_STATUSES } from "./refs";

interface Tool { id: number; code: string; name: string; kind: string; status: string; work_centre_name: string;
  used_percent: string | null; remaining: string | null; is_worn: boolean; [key: string]: unknown }

const named = (choices: [string, string][], key: string) => choices.find(([value]) => value === key)?.[1] ?? key;

const columns: Column<Tool>[] = [
  { key: "code", label: "Tool", sort: "code", width: "9rem" },
  { key: "name", label: "Name" },
  { key: "kind", label: "Kind", width: "10rem", render: (row) => named(TOOL_KINDS, row.kind) },
  { key: "work_centre_name", label: "Bank", width: "10rem" },
  { key: "used_percent", label: "Life used %", kind: "quantity", width: "8rem" },
  { key: "status", label: "", width: "10rem", render: (row) => (row.is_worn ? "Worn out" : named(TOOL_STATUSES, row.status)) },
];

export default function Tools() {
  return (
    <ListView<Tool>
      title="Tools"
      noun={["tool", "tools"]}
      endpoint="/api/manufacturing/tools/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/tools/${row.id}`}
      searchHint="Code or name"
      facets={[{ label: "Cylinders", params: { kind: "cylinder" } }, { label: "Available", params: { status: "available" } },
        { label: "Away for service", params: { status: "service" } }]}
      create={{ href: "/making/tools/new", permission: "manufacturing.add_tool" }}
    />
  );
}
