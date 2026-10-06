import { RecordScreen, type FieldDef } from "../../views/RecordScreen";

type Row = Record<string, unknown>;

const REASON: FieldDef["ref"] = { endpoint: "/api/inventory/adjustment-reasons/", permission: "inventory.view_adjustmentreason", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };
const ACCOUNT: FieldDef["pick"] = { endpoint: "/api/accounting/accounts/", permission: "accounting.view_account", label: (row: Row) => `${String(row.code)} · ${String(row.name)}` };

/** Where the manufacturing side of the ledger lands: work in progress, variances, absorbed conversion, scrap and revaluation. */
export default function ManufacturingSettingsForm() {
  return (
    <RecordScreen
      endpoint="/api/manufacturing/manufacturing-settings/"
      back="/making/manufacturing-settings"
      backLabel="Manufacturing settings"
      newTitle="New manufacturing settings"
      heading={() => "Manufacturing settings"}
      permissions={{ change: "manufacturing.change_manufacturingsettings" }}
      fields={[
        { key: "scrap_needs_reason", label: "Scrap needs reason", kind: "bool", initial: false, hint: "Every sack written off says why" },
        { key: "wip_account", label: "Work in progress account", kind: "pick", pick: ACCOUNT, hint: "Holds what has been fed into open runs. It cannot move while a run is open" },
        { key: "variance_account", label: "Variance account", kind: "pick", pick: ACCOUNT, hint: "Where a closed run's over- or under-consumption lands" },
        { key: "conversion_absorbed_account", label: "Conversion absorbed account", kind: "pick", pick: ACCOUNT, hint: "Credited when machine time is charged to a run" },
        { key: "conversion_variance_account", label: "Conversion variance account", kind: "pick", pick: ACCOUNT, hint: "Where a run's time overrun lands at close, kept apart from the material variance" },
        { key: "revaluation_account", label: "Revaluation account", kind: "pick", pick: ACCOUNT, hint: "Where a standard cost change lands" },
        { key: "scrap_account", label: "Scrap account", kind: "pick", pick: ACCOUNT, hint: "Where production that failed is written off" },
        { key: "spares_reason", label: "Spares reason", kind: "ref", ref: REASON, hint: "What spare parts issued to a maintenance job are written off under, and so which expense account they land in" },
      ]}
    />
  );
}
