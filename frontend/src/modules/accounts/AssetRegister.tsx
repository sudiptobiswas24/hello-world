import type { Column } from "../../views/DataTable";
import { ReportView, type ParamDef } from "../../views/ReportView";

interface Row { asset: string; name: string; category: string; state: string; cost: string; accumulated: string; net_book_value: string; monthly_charge: string; [key: string]: unknown }

const PARAMS: ParamDef[] = [
  { key: "as_of", label: "As of", kind: "date", initial: "today" },
  { key: "category", label: "Category", kind: "ref",
    ref: { endpoint: "/api/assets/categories/", label: (row) => String(row.name || row.code), value: (row) => String(row.code),
      permission: "assets.view_assetcategory" } },
];

/** Every asset's cost, depreciation and book value as they stood on a day: what the books say the plant owns. */
export default function AssetRegister() {
  const columns: Column<Row>[] = [
    { key: "asset", label: "Asset", width: "9rem" },
    { key: "name", label: "Name" },
    { key: "category", label: "Category", width: "8rem" },
    { key: "state", label: "State", kind: "status", width: "8rem" },
    { key: "cost", label: "Cost", kind: "money", width: "10rem" },
    { key: "accumulated", label: "Depreciated", kind: "money", width: "10rem" },
    { key: "net_book_value", label: "Book value", kind: "money", width: "10rem" },
    { key: "monthly_charge", label: "A month", kind: "money", width: "9rem" },
  ];
  return (
    <ReportView<Row[], Row>
      title="Fixed asset register"
      endpoint="/api/assets/assets/register/"
      params={PARAMS}
      rows={(data) => data}
      columns={columns}
      empty="No assets on the books on that day."
    />
  );
}
