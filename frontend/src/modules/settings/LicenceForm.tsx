import { RecordScreen } from "../../views/RecordScreen";

const KINDS: [string, string][] = [
  ["factory", "Factory licence"], ["consent", "Consent to operate (pollution board)"], ["fire", "Fire NOC"],
  ["metrology", "Weights and measures stamping"], ["boiler", "Boiler certificate"], ["trade", "Trade licence"],
  ["other", "Other"],
];
const TONES: Record<string, string> = { valid: "done", due: "open", lapsed: "cancelled", renewed: "draft" };

/**
 * One licence and when to start renewing it. Renewed, it is a new record
 * with its own number and dates, and this one points to it.
 */
export default function LicenceForm() {
  return (
    <RecordScreen
      endpoint="/api/core/licences/"
      back="/settings/licences"
      backLabel="Licences"
      newTitle="New licence"
      heading={(row) => `${String(row.kind_label ?? "")} · ${String(row.licence_number ?? "")}`}
      state={(row) => (row.status ? { label: String(row.status), tone: TONES[String(row.status)] ?? "draft" } : null)}
      permissions={{ add: "core.add_licence", change: "core.change_licence", delete: "core.delete_licence" }}
      fields={[
        { key: "kind", label: "Licence", kind: "choice", choices: KINDS },
        { key: "licence_number", label: "Number" },
        { key: "issued_by", label: "Issued by", hint: "The office that renews it" },
        { key: "covers", label: "For", hint: "Where the plant holds several: \"Weighbridge WB-1\"" },
        { key: "valid_from", label: "Valid from", kind: "date" },
        { key: "valid_to", label: "Valid to", kind: "date" },
        { key: "remind_days", label: "Start renewing, days before", kind: "integer", initial: 60 },
        { key: "renew_from", label: "Renew from", kind: "date", readOnly: true, existingOnly: true },
        { key: "renewed_by_number", label: "Renewed by", readOnly: true, existingOnly: true },
        { key: "note", label: "Note", wide: true },
      ]}
      actions={[
        { label: "Renew", path: "renew", permission: "core.add_licence", primary: true, done: "Renewal recorded",
          when: (row) => !row.renewed_by,
          fields: [
            { key: "licence_number", label: "New number" },
            { key: "valid_from", label: "Valid from", kind: "date" },
            { key: "valid_to", label: "Valid to", kind: "date" },
          ],
          then: (renewal) => `/settings/licences/${String(renewal.id)}` },
      ]}
    />
  );
}
