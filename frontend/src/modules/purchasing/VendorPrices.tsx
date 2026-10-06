import { ListView, type Column } from "../../views/ListView";

type Row = Record<string, unknown> & { id: number };

const columns: Column<Row>[] = [
  { key: "item_label", label: "Item" },
  { key: "vendor_name", label: "Vendor" },
  { key: "min_quantity", label: "From quantity", kind: "quantity" },
  { key: "unit_price", label: "Price", kind: "money" },
  { key: "currency_code", label: "Currency" },
  { key: "valid_from", label: "From", kind: "date" },
  { key: "valid_to", label: "To", kind: "date" },
  { key: "is_preferred", label: "Preferred" },
];

export default function VendorPrices() {
  return (
    <ListView<Row>
      title="Vendor prices"
      noun={["vendor price", "vendor prices"]}
      endpoint="/api/purchasing/vendor-prices/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/purchasing/vendor-prices/${row.id}`}
      searchHint="Item, vendor or their part number"
      create={{ href: "/purchasing/vendor-prices/new", permission: "purchasing.add_vendorprice" }}
    />
  );
}
