import { ListView, type Column } from "../../views/ListView";

interface Route { id: number; from_name: string; to_name: string; lead_days: number; priority: number; is_active: boolean; [key: string]: unknown }

const columns: Column<Route>[] = [
  { key: "from_name", label: "From" },
  { key: "to_name", label: "To" },
  { key: "lead_days", label: "Days on the road", kind: "quantity", width: "10rem", render: (row) => String(row.lead_days) },
  { key: "priority", label: "Preference", kind: "quantity", width: "8rem", render: (row) => String(row.priority) },
];

/** Where a warehouse is restocked from by transfer, and how long it takes. */
export default function Routes() {
  return (
    <ListView<Route>
      title="Transfer routes"
      noun={["route", "routes"]}
      endpoint="/api/planning/transfer-routes/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/routes/${row.id}`}
      create={{ href: "/production/routes/new", permission: "planning.add_transferroute" }}
    />
  );
}
