import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

// One record, made the first time it is read, changed in place (O626).
const columns: Column<Row>[] = [
  { key: "horizon_days", label: "Horizon (days)", kind: "quantity" },
  { key: "planning_fence_days", label: "Planning fence (days)", kind: "quantity" },
];

export default function PlanningSettingsList() {
  return (
    <ListView<Row>
      title="Planning settings"
      noun={["planning settings", "planning settings"]}
      endpoint="/api/planning/settings/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/production/planning-settings/${row.id}`}
      searchHint="Nothing to search"
    />
  );
}
