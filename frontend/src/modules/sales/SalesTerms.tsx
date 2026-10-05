import { useState } from "react";

import { useAct, useReference, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { DecimalInput } from "../../forms/fields";
import { money } from "../../lib/format";

interface Profile {
  id: number;
  party: number;
  sales_rep: number | null;
  sales_rep_name: string;
  credit_limit: string | null;
  [key: string]: unknown;
}

interface Rep { id: number; party: number; name: string; is_active: boolean }

const PROFILES = "/api/sales/customer-profiles/";

/**
 * Whose customer this is and how far they may owe. Read by anyone who may
 * read the profile; changed by whoever may change it, which is not the
 * rep: a rep cannot hand their customer to someone else, or take one.
 */
export function SalesTerms({ party }: { party: number }) {
  const { can } = useAccess();
  const readable = can("sales.view_customerprofile");
  const profiles = useRows<Profile>(PROFILES, { party }, readable);
  const profile = profiles.data?.[0];
  const mayChange = profile ? can("sales.change_customerprofile") : can("sales.add_customerprofile");
  const reps = useReference<Rep>("/api/sales/sales-reps/", { is_active: "true" }, mayChange && can("sales.view_salesrep"));
  const act = useAct<Profile>();
  const [rep, setRep] = useState<string | null>(null);
  const [limit, setLimit] = useState<string | null>(null);
  if (!readable || profiles.isPending) return null;

  const chosenRep = rep ?? (profile?.sales_rep ? String(profile.sales_rep) : "");
  const chosenLimit = limit ?? profile?.credit_limit ?? "";
  const changed = rep !== null || limit !== null;
  const save = async () => {
    const body = { sales_rep: chosenRep ? Number(chosenRep) : null, credit_limit: chosenLimit || null };
    const outcome = profile
      ? await act.run("PATCH", `${PROFILES}${profile.id}/`, body, { done: "Saved" })
      : await act.run("POST", PROFILES, { party, ...body }, { done: "Saved" });
    if (outcome.ok) {
      setRep(null);
      setLimit(null);
    }
  };

  return (
    <section className="related-list" aria-label="Sales terms">
      <h2>Sales terms</h2>
      {mayChange ? (
        <form className="field-grid" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <label className="field">Sales rep
            <select value={chosenRep} onChange={(e) => setRep(e.target.value)}>
              <option value="">Nobody's</option>
              {(reps.data ?? []).map((row) => <option key={row.id} value={row.party}>{row.name}</option>)}
            </select>
          </label>
          <label className="field">Credit limit
            <DecimalInput places={2} value={chosenLimit} onChange={setLimit} placeholder="No limit" />
          </label>
          {changed && <button type="submit" className="btn primary" disabled={act.pending}>Save</button>}
        </form>
      ) : (
        <ul>
          <li><span>Sales rep</span><span>{profile?.sales_rep_name || "Nobody's"}</span></li>
          <li><span>Credit limit</span><span>{profile?.credit_limit ? money(profile.credit_limit) : "No limit"}</span></li>
        </ul>
      )}
    </section>
  );
}
