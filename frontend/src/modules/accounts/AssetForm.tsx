import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const CATEGORY: FieldDef["ref"] = { endpoint: "/api/assets/categories/", permission: "assets.view_assetcategory", label: (row: Row) => String(row.name || row.code) };
const VENDOR: FieldDef["pick"] = {
  endpoint: "/api/core/parties/", permission: "core.view_party", query: { role_assignments__role: "vendor" },
  label: (row: Row) => `${String(row.code)} · ${String(row.name)}`,
};
const draft = (row: Row) => row.status === "draft";
const inService = (row: Row) => row.status === "in_service";

/**
 * A machine, a building, a vehicle: capitalised at cost, depreciated
 * monthly from the day it went into service, and taken off the books when
 * it goes. Each of those posts, and takes the right to post.
 */
export default function AssetForm() {
  return (
    <RecordScreen
      endpoint="/api/assets/assets/"
      back="/accounts/assets"
      backLabel="Fixed assets"
      newTitle="New fixed asset"
      heading={(row) => `${String(row.number || "Draft")} · ${String(row.name ?? "")}`}
      state={(row) => ({ label: String(row.status ?? "").replace(/_/g, " "), tone: draft(row) ? "draft" : inService(row) ? "open" : "done" })}
      permissions={{ add: "assets.add_fixedasset", change: "assets.change_fixedasset", delete: "assets.delete_fixedasset" }}
      editable={draft}
      fields={[
        { key: "name", label: "Name" },
        { key: "category", label: "Category", kind: "ref", ref: CATEGORY },
        { key: "vendor", label: "Bought from", kind: "pick", pick: VENDOR, show: (row) => String(row.vendor_name || "—") },
        { key: "acquisition_date", label: "Bought on", kind: "date" },
        { key: "cost", label: "Cost", kind: "money" },
        { key: "salvage_value", label: "Salvage value", kind: "money", initial: "0" },
        { key: "life_months", label: "Life (months)", kind: "integer", hint: "The category's, if left empty" },
        { key: "in_service_date", label: "In service from", readOnly: true, kind: "date" },
        { key: "accumulated", label: "Depreciated so far", readOnly: true, kind: "money" },
        { key: "net_book_value", label: "Book value", readOnly: true, kind: "money" },
        { key: "monthly_charge", label: "A month's charge", readOnly: true, kind: "money" },
        { key: "disposed_on", label: "Disposed of", readOnly: true, kind: "date" },
      ]}
      actions={[
        { label: "Put into service", path: "place-in-service", permission: "accounting.post_journalentry", primary: true,
          when: draft, done: "In service", fields: [{ key: "on_date", label: "From", kind: "date" }] },
        { label: "Charge depreciation", path: "depreciate", permission: "accounting.post_journalentry",
          when: inService, done: "Depreciation posted", fields: [{ key: "through", label: "Through", kind: "date" }] },
        { label: "Dispose of it", path: "dispose", permission: "assets.dispose_fixedasset", danger: true,
          when: inService, done: "Disposed of", fields: [
            { key: "on_date", label: "On", kind: "date" },
            { key: "proceeds", label: "Sold for", kind: "money", hint: "Nothing, if scrapped; invoice the buyer separately" },
            { key: "memo", label: "Why", wide: true },
          ] },
        { label: "Undo the capitalisation", path: "uncapitalise", permission: "accounting.post_journalentry", danger: true,
          when: (row) => Boolean(row.bill_line) && !row.disposed_on, done: "Uncapitalised",
          fields: [{ key: "on_date", label: "On", kind: "date" }] },
      ]}
      panels={[{
        title: "Depreciation charged", permission: "assets.view_depreciationentry", endpoint: "/api/assets/depreciation/",
        query: (asset) => ({ asset: String(asset.id) }),
        columns: [
          { key: "period_end", label: "Month to", kind: "date", width: "9rem" },
          { key: "amount", label: "Charge", kind: "money", width: "10rem" },
          { key: "reversal", label: "", render: (row) => (row.reversal ? "Reversed" : "") },
        ],
      }]}
    />
  );
}
