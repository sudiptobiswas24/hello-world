import { RecordScreen } from "../../views/RecordScreen";

/** A currency documents may be in. The base currency is the one the books are kept in. */
export default function CurrencyForm() {
  return (
    <RecordScreen
      endpoint="/api/core/currencies/"
      back="/settings/currencies"
      backLabel="Currencies"
      newTitle="New currency"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "core.add_currency", change: "core.change_currency", delete: "core.delete_currency" }}
      fields={[
        { key: "code", label: "Code", hint: "ISO 4217 code, e.g. USD" },
        { key: "name", label: "Name" },
        { key: "symbol", label: "Symbol" },
        { key: "decimal_places", label: "Decimal places", kind: "integer", initial: 2 },
        { key: "is_base", label: "Base", kind: "bool", initial: false, hint: "The company's single reporting/functional currency" },
      ]}
    />
  );
}
