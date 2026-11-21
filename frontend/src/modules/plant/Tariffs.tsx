import { date, money } from "../../lib/format";
import { ListView, type Column } from "../../views/ListView";

interface Tariff { id: number; rate: string; valid_from: string; note: string; [key: string]: unknown }

const columns: Column<Tariff>[] = [
  { key: "valid_from", label: "From", width: "9rem", render: (row) => date(row.valid_from) },
  { key: "rate", label: "Rupees a kWh", kind: "money", width: "10rem", render: (row) => money(row.rate, 4) },
  { key: "note", label: "Note" },
];

export default function Tariffs() {
  return (
    <ListView<Tariff>
      title="Electricity tariffs"
      noun={["tariff", "tariffs"]}
      endpoint="/api/manufacturing/energy-tariffs/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/plant/tariffs/${row.id}`}
      create={{ href: "/plant/tariffs/new", permission: "manufacturing.add_energytariff" }}
    />
  );
}
