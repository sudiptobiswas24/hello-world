import { ListView, type Column } from "../../views/ListView";

interface Family { id: number; item_label: string; work_centre_name: string; family: string; [key: string]: unknown }

const columns: Column<Family>[] = [
  { key: "family", label: "Family", width: "12rem" },
  { key: "item_label", label: "Item" },
  { key: "work_centre_name", label: "On" },
];

export default function SetupFamilies() {
  return (
    <ListView<Family>
      title="Setup families"
      noun={["setup family", "setup families"]}
      endpoint="/api/manufacturing/setup-families/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/making/setup-families/${row.id}`}
      searchHint="Family or item"
      create={{ href: "/making/setup-families/new", permission: "manufacturing.add_setupfamily" }}
    />
  );
}
