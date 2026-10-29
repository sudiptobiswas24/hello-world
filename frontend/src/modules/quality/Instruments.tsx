import { ListView, type Column } from "../../views/ListView";

interface Instrument { id: number; code: string; name: string; location: string; interval_days: number; status: string; due_on: string | null; [key: string]: unknown }

const columns: Column<Instrument>[] = [
  { key: "code", label: "Code", width: "9rem", sort: "code" },
  { key: "name", label: "Instrument" },
  { key: "location", label: "Where", width: "10rem" },
  { key: "interval_days", label: "Every (days)", width: "8rem", kind: "quantity", render: (row) => String(row.interval_days) },
];

/** Scales and testers, and how often each is calibrated. */
export default function Instruments() {
  return (
    <ListView<Instrument>
      title="Instruments"
      noun={["instrument", "instruments"]}
      endpoint="/api/quality/instruments/"
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `/quality/instruments/${row.id}`}
      searchHint="Code, name, serial"
      facets={[{ label: "Active", params: { is_active: "true" } }]}
      create={{ href: "/quality/instruments/new", permission: "quality.add_instrument" }}
    />
  );
}
