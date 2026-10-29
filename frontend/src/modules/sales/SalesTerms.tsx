import { useAct, useReference, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { FieldSection } from "../../forms/FieldSection";
import { Field } from "../../forms/fields";
import { useDraft } from "../../forms/useDraft";
import { useRefs } from "../../views/RecordScreen";

import { TERM_FIELDS, TERM_SECTIONS } from "./customerTerms";

interface Profile {
  id: number;
  party: number;
  sales_rep: number | null;
  sales_rep_name: string;
  [key: string]: unknown;
}

interface Rep { id: number; party: number; name: string; is_active: boolean }

const PROFILES = "/api/sales/customer-profiles/";

/**
 * Every term the customer trades on: credit, delivery, packing, quality
 * and quantity, and whose customer they are. Read by anyone who may read
 * the profile; changed by whoever may change it, which is not the rep: a
 * rep cannot hand their customer to someone else, or lift a credit hold.
 */
export function SalesTerms({ party }: { party: number }) {
  const { can } = useAccess();
  const readable = can("sales.view_customerprofile");
  const profiles = useRows<Profile>(PROFILES, { party }, readable);
  const profile = profiles.data?.[0];
  const mayChange = profile ? can("sales.change_customerprofile") : can("sales.add_customerprofile");
  const reps = useReference<Rep>("/api/sales/sales-reps/", { is_active: "true" }, mayChange && can("sales.view_salesrep"));
  const refs = useRefs(TERM_FIELDS);
  const draft = useDraft<Profile>(profile ?? ({ party } as Profile));
  const act = useAct<Profile>();
  if (!readable || profiles.isPending) return null;

  const value = draft.value;
  const save = async () => {
    const outcome = profile
      ? await act.run("PATCH", `${PROFILES}${profile.id}/`, draft.changes, { done: "Saved" })
      : await act.run("POST", PROFILES, { ...draft.changes, party }, { done: "Saved" });
    if (outcome.ok) {
      draft.reset();
      void profiles.refetch();
    } else draft.failed(outcome.error);
  };

  return (
    <section className="related-list" aria-label="Sales terms">
      <h2>Sales terms</h2>
      <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
        {TERM_SECTIONS.map((section, index) => (
          <FieldSection key={section.title} title={section.title} fields={section.fields} value={value}
            set={(key, next) => draft.set(key, next as never)} errors={draft.errors} editable={mayChange} refs={refs}>
            {index === 0 && (
              <Field label="Sales rep" errors={draft.errors.sales_rep} hint="A new order takes them as its rep">
                {(id) => mayChange
                  ? (
                    <select id={id} value={value.sales_rep === null || value.sales_rep === undefined ? "" : String(value.sales_rep)}
                      onChange={(e) => draft.set("sales_rep", (e.target.value ? Number(e.target.value) : null) as never)}>
                      <option value="">Nobody's</option>
                      {(reps.data ?? []).map((row) => <option key={row.id} value={row.party}>{row.name}</option>)}
                    </select>
                  )
                  : <output id={id}>{profile?.sales_rep_name || "Nobody's"}</output>}
              </Field>
            )}
          </FieldSection>
        ))}
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
        {mayChange && draft.dirty && <button type="submit" className="btn primary" disabled={act.pending}>Save terms</button>}
      </form>
    </section>
  );
}
