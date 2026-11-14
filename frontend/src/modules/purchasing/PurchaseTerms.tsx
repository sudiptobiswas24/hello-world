import { useAct, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { FieldSection } from "../../forms/FieldSection";
import { useDraft } from "../../forms/useDraft";
import { useRefs } from "../../views/RecordScreen";

import { VENDOR_TERM_FIELDS, VENDOR_TERM_SECTIONS } from "./vendorTerms";

interface Profile {
  id: number;
  party: number;
  [key: string]: unknown;
}

const PROFILES = "/api/purchasing/vendor-profiles/";

/**
 * What we buy from the vendor on, and whether we buy from them at all.
 * Read by anyone who may read the profile; the terms changed by whoever
 * may change it, and each control only by whoever holds it: the buyer
 * sees a vendor is blocked and why, and cannot lift it.
 */
export function PurchaseTerms({ party }: { party: number }) {
  const { can } = useAccess();
  const readable = can("purchasing.view_vendorprofile");
  const profiles = useRows<Profile>(PROFILES, { party }, readable);
  const profile = profiles.data?.[0];
  const mayChange = profile ? can("purchasing.change_vendorprofile") : can("purchasing.add_vendorprofile");
  const refs = useRefs(VENDOR_TERM_FIELDS);
  const draft = useDraft<Profile>(profile ?? ({ party, standing: "approved", payment_hold: false } as unknown as Profile));
  const act = useAct<Profile>();
  if (!readable || profiles.isPending) return null;

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
    <section className="related-list" aria-label="Purchase terms">
      <h2>Purchase terms</h2>
      <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
        {VENDOR_TERM_SECTIONS.map((section) => (
          <FieldSection key={section.title} title={section.title} fields={section.fields} value={draft.value}
            set={(key, next) => draft.set(key, next as never)} errors={draft.errors} refs={refs}
            editable={mayChange && (!section.control || can(section.control))} />
        ))}
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
        {mayChange && draft.dirty && <button type="submit" className="btn primary" disabled={act.pending}>Save terms</button>}
      </form>
    </section>
  );
}
