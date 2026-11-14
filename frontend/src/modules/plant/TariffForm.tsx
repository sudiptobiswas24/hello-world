import { RecordScreen } from "../../views/RecordScreen";

/** A rate per kWh from a date: what each reading's electricity is costed at. */
export default function TariffForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/energy-tariffs/"
      back="/plant/tariffs"
      backLabel="Electricity tariffs"
      newTitle="New electricity tariff"
      heading={(row) => `From ${String(row.valid_from)}`}
      permissions={{ add: "manufacturing.add_energytariff", change: "manufacturing.change_energytariff",
        delete: "manufacturing.delete_energytariff" }}
      fields={[
        { key: "valid_from", label: "From", kind: "date" },
        { key: "rate", label: "Rupees a kWh", kind: "decimal", places: 4 },
        { key: "note", label: "Note", kind: "text" },
      ]}
    />
  );
}
