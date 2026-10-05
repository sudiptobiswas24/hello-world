import { useNavigate, useParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { Field } from "../../forms/fields";
import { useDraft } from "../../forms/useDraft";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { RelatedList } from "./Related";

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
  ["tax_id", "GSTIN"],
  ["phone", "Phone"],
  ["email", "Email", "Where invoices and statements are sent"],
];

/** A customer: who they are, and what has passed between us. */
export default function CustomerForm() {
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
      ? await act.run("POST", ENDPOINT, { ...value, role: "customer" }, { done: "Customer added" })
      : await act.run("PATCH", `${ENDPOINT}${party!.id}/`, draft.changes, { done: "Saved" });
    if (outcome.ok) {
      draft.reset();
      if (isNew) navigate(`/sales/customers/${outcome.data.id}`, { replace: true });
    } else draft.failed(outcome.error);
  };

  return (
    <article className="doc">
      <DocHeader back="/sales/customers" backLabel="Customers" title="New customer" number={party?.name}
        state={party ? (party.is_active ? "Active" : "Archived") : undefined} tone={party?.is_active ? "done" : "draft"}>
        {editable && (draft.dirty || isNew) && <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Add customer" : "Save"}</ActionButton>}
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
      {party && (
        <div className="related">
          <RelatedList title="Open orders" endpoint="/api/sales/sales-orders/" permission="sales.view_salesorder" query={{ customer: party.id, status: "confirmed" }}
            href={(row) => `/sales/orders/${row.id}`}
            cells={(row) => [String(row.number), date(String(row.order_date)), money(String(row.total))]} />
          <RelatedList title="Invoices not yet paid" endpoint="/api/sales/invoices/" permission="sales.view_invoice" query={{ customer: party.id, open: "true" }}
            href={(row) => `/sales/invoices/${row.id}`}
            cells={(row) => [String(row.number), date(String(row.due_date)), money(String(row.amount_due))]} />
        </div>
      )}
    </article>
  );
}
