import { useNavigate, useParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { useDraft } from "../../forms/useDraft";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { RecordPanel } from "../../views/RecordScreen";
import type { ReactNode } from "react";

import { PARTY_PANELS } from "./panels";

import type { PartyRole } from "../../forms/PartyPicker";

interface Party {
  id: number;
  code: string;
  name: string;
  legal_name: string;
  email: string;
  phone: string;
  tax_id: string;
  is_active: boolean;
  [key: string]: unknown;
}

const ENDPOINT = "/api/core/parties/";
const FIELDS: [keyof Party & string, string, string?][] = [
  ["code", "Code", "Short and unique: what people type to find them"],
  ["name", "Name"],
  ["legal_name", "Legal name", "As it must appear on invoices, if different"],
  ["tax_id", "Other tax number", "PAN, or a foreign tax number. The GST registration is kept under GST, below"],
  ["phone", "Phone"],
  ["email", "Email", "Where invoices and statements are sent"],
];

export interface PartyConfig {
  role: PartyRole;
  base: string; // "/sales/customers"
  plural: string; // "Customers"
  /** What has passed between us: lists of their documents. */
  related?: (party: { id: number }) => ReactNode;
}

/** A customer or a vendor: who they are, and what has passed between us. */
export function PartyForm({ role, base, plural, related }: PartyConfig) {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Party>(ENDPOINT, id);
  const party = record.data;
  const blank = { code: "", name: "", legal_name: "", email: "", phone: "", tax_id: "", is_active: true } as Party;
  const draft = useDraft<Party>(isNew ? blank : party);
  const act = useAct<Party>();

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !party) return <div className="loading">Opening…</div>;

  const editable = isNew ? can("core.add_party") : can("core.change_party");
  const value = draft.value;
  const save = async () => {
    const outcome = isNew
      // The role travels with the party: made together or not at all.
      ? await act.run("POST", ENDPOINT, { ...value, role }, { done: `${role === "customer" ? "Customer" : "Vendor"} added` })
      : await act.run("PATCH", `${ENDPOINT}${party!.id}/`, draft.changes, { done: "Saved" });
    if (outcome.ok) {
      draft.reset();
      if (isNew) navigate(`${base}/${outcome.data.id}`, { replace: true });
    } else draft.failed(outcome.error);
  };

  return (
    <article className="doc">
      <DocHeader back={base} backLabel={plural} title={`New ${role}`} number={party?.name}
        state={party ? (party.is_active ? "Active" : "Archived") : undefined} tone={party?.is_active ? "done" : "draft"}>
        {editable && (draft.dirty || isNew) && <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? `Add ${role}` : "Save"}</ActionButton>}
        {party && can("core.change_party") && !draft.dirty && (
          <ActionButton pending={act.pending} onClick={() => void act.run("PATCH", `${ENDPOINT}${party.id}/`, { is_active: !party.is_active },
            { done: party.is_active ? "Archived" : "Restored" })}>
            {party.is_active ? "Archive" : "Restore"}
          </ActionButton>
        )}
      </DocHeader>
      <Sheet>
        <div className="field-grid">
          {FIELDS.map(([key, label, hint]) => (
            <Field key={key} label={label} hint={hint} errors={draft.errors[key]}>
              {(fid, described) => editable
                ? <input id={fid} aria-describedby={described} value={String(value[key] ?? "")} onChange={(e) => draft.set(key, e.target.value as never)} />
                : <output id={fid}>{String(party?.[key] || "—")}</output>}
            </Field>
          ))}
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
      </Sheet>
      {party && PARTY_PANELS.map((panel) => <RecordPanel key={panel.title} panel={panel} record={party} />)}
      {party && related && <div className="related">{related(party)}</div>}
    </article>
  );
}
