import { ListView, type Column } from "../../views/ListView";

interface Item {
  id: number;
  sku: string;
  name: string;
  item_type: string;
  tracking: string;
  hsn_code: string;
  sale_price: string | null;
  is_active: boolean;
}

const TRACKING: Record<string, string> = { none: "—", lot: "By batch", serial: "By serial" };

const columns: Column<Item>[] = [
  { key: "sku", label: "Code", sort: "sku", width: "10rem" },
  { key: "name", label: "Name", sort: "name" },
  { key: "item_type", label: "Kind", width: "9rem", render: (row) => row.item_type.replace(/_/g, " ") },
  { key: "tracking", label: "Tracked", width: "8rem", render: (row) => TRACKING[row.tracking] ?? row.tracking },
  { key: "hsn_code", label: "HSN", width: "7rem" },
  { key: "sale_price", label: "List price", kind: "money", width: "9rem" },
];

export default function Items() {
  return (
    <ListView<Item>
      title="Items"
      noun={["item", "items"]}
      endpoint="/api/inventory/items/"
      columns={columns}
      rowKey={(row) => row.id}
      searchHint="Code, name, HSN"
      facets={[
        { label: "Active", params: { is_active: "true" } },
        { label: "Tracked by batch", params: { tracking: "lot" } },
      ]}
    />
  );
}
