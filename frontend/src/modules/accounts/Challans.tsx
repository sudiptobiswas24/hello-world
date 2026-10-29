import { ListView, type Column } from "../../views/ListView";
import { RecordScreen, type FieldDef } from "../../views/RecordScreen";
import { MONEY_ACCOUNT, SECTION, TCS_SECTION } from "./refs";

type Row = Record<string, unknown> & { id: number };

/**
 * Tax held for the government and paid over by challan: TDS the plant
 * deducted, TCS it collected. One screen for both, as the server pays both
 * over by one mechanism (accounting.challans): what differs is named here.
 */
export type ChallanKind = {
  title: string;
  endpoint: string;
  path: string;
  section: FieldDef["ref"];
  add: string;
  voids: string;
  month: string;
  monthHint: string;
  newTitle: string;
};

export const TDS: ChallanKind = {
  title: "TDS challans", endpoint: "/api/purchasing/tds-challans/", path: "/accounts/tds-challans", section: SECTION,
  add: "purchasing.add_tdschallan", voids: "purchasing.post_bill", month: "Deducted in",
  monthHint: "Any day of the month; all of it is paid", newTitle: "Pay TDS over",
};

export const TCS: ChallanKind = {
  title: "TCS challans", endpoint: "/api/sales/tcs-challans/", path: "/accounts/tcs-challans", section: TCS_SECTION,
  add: "sales.add_tcschallan", voids: "sales.post_invoice", month: "Collected to",
  monthHint: "Any day of the month: everything collected to its end and not yet paid, less what credit notes gave back",
  newTitle: "Pay TCS over",
};

const columns: Column<Row>[] = [
  { key: "date", label: "Paid", kind: "date", width: "8rem" },
  { key: "section_code", label: "Section", width: "6rem" },
  { key: "month", label: "For", width: "8rem", render: (row) => String(row.month ?? "").slice(0, 7) },
  { key: "form", label: "Challan", width: "9rem" },
  { key: "challan_number", label: "Number", width: "9rem" },
  { key: "bsr_code", label: "BSR", width: "8rem" },
  { key: "tan", label: "TAN", width: "8rem" },
  { key: "amount", label: "Amount", kind: "money", width: "10rem" },
  { key: "voided", label: "", width: "6rem", render: (row) => (row.voided ? "Voided" : "") },
];

/** Tax paid over to the government, a month and a section at a time. */
export function ChallanList({ kind }: { kind: ChallanKind }) {
  return (
    <ListView<Row>
      title={kind.title}
      noun={["challan", "challans"]}
      endpoint={kind.endpoint}
      columns={columns}
      rowKey={(row) => row.id}
      rowHref={(row) => `${kind.path}/${row.id}`}
      searchHint="Challan or BSR"
      create={{ href: `${kind.path}/new`, permission: kind.add }}
    />
  );
}

/**
 * A month paid over: everything waiting under the section goes on it. Paid
 * is a fact; a challan that did not go through is voided, and what it paid
 * waits for the next.
 */
export function ChallanForm({ kind }: { kind: ChallanKind }) {
  return (
    <RecordScreen
      endpoint={kind.endpoint}
      createUrl={`${kind.endpoint}pay/`}
      back={kind.path}
      backLabel={kind.title}
      newTitle={kind.newTitle}
      heading={(row) => `${String(row.section_code ?? "")} · ${String(row.bsr_code ?? "")}/${String(row.challan_number ?? "")}`}
      state={(row) => (row.voided ? { label: "Voided", tone: "void" } : { label: "Paid", tone: "done" })}
      permissions={{ add: kind.add }}
      fields={[
        { key: "section", label: "Section", kind: "ref", ref: kind.section, show: (row) => String(row.section_code) },
        { key: "month", label: kind.month, kind: "date", hint: kind.monthHint },
        { key: "date", label: "Paid on", kind: "date" },
        { key: "bank_account", label: "From", kind: "pick", pick: MONEY_ACCOUNT },
        { key: "challan_number", label: "Challan number" },
        { key: "bsr_code", label: "BSR code", hint: "The bank branch's seven digits" },
        { key: "amount", label: "Amount", kind: "money", existingOnly: true, readOnly: true },
        { key: "form", label: "Challan", existingOnly: true, readOnly: true },
        { key: "tan", label: "TAN", existingOnly: true, readOnly: true },
      ]}
      actions={[
        { label: "Void", path: "void", permission: kind.voids, danger: true, when: (row) => !row.voided,
          done: "Voided: the tax is owed again" },
      ]}
    />
  );
}
