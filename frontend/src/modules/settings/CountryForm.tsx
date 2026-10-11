import { RecordScreen } from "../../views/RecordScreen";

/** A country, for addresses and fiscal positions. */
export default function CountryForm() {
  return (
    <RecordScreen
      endpoint="/api/core/countries/"
      back="/settings/countries"
      backLabel="Countries"
      newTitle="New country"
      heading={(row) => String(row.code ?? "") + " · " + String(row.name ?? "")}
      permissions={{ add: "core.add_country", change: "core.change_country", delete: "core.delete_country" }}
      fields={[
        { key: "code", label: "Code", hint: "ISO 3166-1 alpha-2, e.g. US" },
        { key: "name", label: "Name" },
      ]}
    />
  );
}
