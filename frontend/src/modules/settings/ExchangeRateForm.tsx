import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const CURRENCY: FieldDef["ref"] = { endpoint: "/api/core/currencies/", permission: "core.view_currency", label: (row: Row) => String(row.code) };

/** What one unit of a currency is worth in the base currency, from a date. A posted document keeps the rate it was posted at. */
export default function ExchangeRateForm() {
  return (
    <RecordScreen
      endpoint="/api/core/exchange-rates/"
      back="/settings/exchange-rates"
      backLabel="Exchange rates"
      newTitle="New exchange rate"
      heading={(row) => String(row.valid_from ?? "")}
      permissions={{ add: "core.add_exchangerate", change: "core.change_exchangerate", delete: "core.delete_exchangerate" }}
      fields={[
        { key: "currency", label: "Currency", kind: "ref", ref: CURRENCY },
        { key: "rate", label: "Rate", kind: "decimal", places: 8, hint: "Units of the base currency per 1 unit of this currency" },
        { key: "valid_from", label: "From", kind: "date" },
      ]}
    />
  );
}
