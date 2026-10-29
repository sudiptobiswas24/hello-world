import { date, quantity } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Entry { id: number; week_of: string; item_label: string; warehouse_name: string; quantity: string; reason: string; committed_at: string | null; withdrawn_at: string | null; [key: string]: unknown }

const columns: Column<Entry>[] = [
  { key: "week_of", label: "Week of", width: "8rem", sort: "week_of", render: (row) => date(row.week_of) },
  { key: "item_label", label: "Item" },
  { key: "warehouse_name", label: "Warehouse", width: "10rem" },
  { key: "quantity", label: "Quantity", kind: "quantity", width: "9rem", render: (row) => quantity(row.quantity) },
  { key: "reason", label: "Why" },
  { key: "state", label: "State", width: "8rem", render: (row) => (row.withdrawn_at ? "Withdrawn" : row.committed_at ? "Committed" : "Draft") },
];

/** Build-ahead by the week: made before the orders arrive, for the season or a campaign. */
export default function MasterSchedule() {
  return (
    <ListView<Entry>
      title="Master schedule"
      noun={["week", "weeks"]}
      endpoint="/api/planning/master-schedule/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/master-schedule/${row.id}`}
      searchHint="Item, why"
      create={{ href: "/production/master-schedule/new", permission: "planning.add_masterscheduleentry" }}
    />
  );
}
