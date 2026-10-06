import { RecordScreen } from "../../views/RecordScreen";

type Row = Record<string, unknown> & { id: number };

/** A published polymer price a price-variation clause follows. */
export default function PriceIndexForm() {
  return (
    <RecordScreen
      endpoint="/api/sales/price-indices/"
      back="/sales/price-indices"
      backLabel="Price indices"
      newTitle="New price index"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "sales.add_priceindex" }}
      fields={[
        { key: "code", label: "Code" },
        { key: "name", label: "Name" },
      ]}
      panels={[{
        // What the index stood at, from a date: a clause bills the change
        // from its base to the value in force on each delivery.
        title: "Values", permission: "sales.view_priceindexvalue", endpoint: "", query: () => ({}),
        read: { path: (index) => `/api/sales/price-indices/${String(index.id)}/values/`, rows: (data) => data as Row[] },
        columns: [
          { key: "valid_from", label: "From", kind: "date", width: "9rem" },
          { key: "value", label: "Value", kind: "quantity" },
        ],
        adder: { label: "Publish a value", permission: "sales.add_priceindexvalue",
          url: (index) => `/api/sales/price-indices/${String(index.id)}/values/`,
          fields: [
            { key: "valid_from", label: "From", kind: "date" },
            { key: "value", label: "Value", kind: "decimal" },
          ],
          body: (values) => values },
      }]}
    />
  );
}
