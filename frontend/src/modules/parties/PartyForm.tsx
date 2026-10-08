import { useNavigate, useParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ExtraFields } from "../../forms/ExtraFields";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { FieldSection } from "../../forms/FieldSection";
import { useDraft } from "../../forms/useDraft";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { RecordPanel, useRefs } from "../../views/RecordScreen";
import type { ReactNode } from "react";

import { PARTY_PANELS } from "./panels";
import { PARTY_FIELDS } from "./partyFields";

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
  const refs = useRefs(PARTY_FIELDS);

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
        <FieldSection fields={PARTY_FIELDS} value={value} set={(key, next) => draft.set(key, next as never)}
          errors={draft.errors} editable={editable} refs={refs} />
        <ExtraFields kind="core.party" value={value.extra as Record<string, unknown> | undefined} set={(next) => draft.set("extra", next as never)} errors={draft.errors} editable={editable} />
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
      </Sheet>
      {party && PARTY_PANELS.map((panel) => <RecordPanel key={panel.title} panel={panel} record={party} />)}
      {party && related && <div className="related">{related(party)}</div>}
    </article>
  );
}
