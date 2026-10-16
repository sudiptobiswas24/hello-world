import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Totals } from "../../forms/Document";
import { DecimalInput, Field, today } from "../../forms/fields";
import { RecordPicker } from "../../forms/RecordPicker";
import { useDraft } from "../../forms/useDraft";
import { minus, positive, sum } from "../../lib/decimal";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { Trail } from "../../views/Trail";

interface Centre { id: number; code: string; name: string }

interface Line {
  id: number;
  account: number;
  account_code: string;
  account_name: string;
  party_name: string;
  debit: string;
  credit: string;
  description: string;
  cost_centre?: number | null;
  cost_centre_name?: string;
}

interface Entry {
  id: number;
  date: string;
  reference: string;
  memo: string;
  posted: boolean;
  reverses: number | null;
  /** The document that posted it and keeps it, on the entry's own page: corrected there, not here. */
  posted_by?: string | null;
  lines: Line[];
  [key: string]: unknown;
}

interface Account {
  id: number;
  code: string;
  name: string;
}

const ENDPOINT = "/api/accounting/journal-entries/";
const LINES = "/api/accounting/journal-lines/";

/**
 * A journal entry: balanced lines, posted once and never edited after;
 * a mistake is reversed, which posts its mirror. Most entries are made
 * by the documents that book them; this is for the ones a bookkeeper
 * makes by hand.
 */
export default function JournalForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Entry>(ENDPOINT, id);
  const entry = record.data;
  const draft = useDraft<Entry>(isNew ? ({ date: today(), reference: "", memo: "" } as unknown as Entry) : entry);
  const act = useAct<Entry>();
  const [account, setAccount] = useState<number | null>(null);
  const [centre, setCentre] = useState<number | null>(null);
  const [side, setSide] = useState<"debit" | "credit">("debit");
  const [amount, setAmount] = useState("");
  const [words, setWords] = useState("");

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !entry) return <div className="loading">Opening…</div>;

  const editable = isNew || (!entry!.posted && can("accounting.change_journalentry"));
  const value = draft.value;
  const debits = sum((entry?.lines ?? []).map((line) => line.debit));
  const credits = sum((entry?.lines ?? []).map((line) => line.credit));
  const out = minus(debits, credits);

  const save = async () => {
    const outcome = isNew
      ? await act.run("POST", ENDPOINT, { date: value.date, reference: value.reference, memo: value.memo }, { done: "Entry started" })
      : await act.run("PATCH", `${ENDPOINT}${entry!.id}/`, draft.changes, { done: "Saved" });
    if (!outcome.ok) return draft.failed(outcome.error);
    draft.reset();
    if (isNew) navigate(`/accounts/journals/${outcome.data.id}`, { replace: true });
  };
  const addLine = async () => {
    if (!entry || !account || !positive(amount || "0")) return;
    const outcome = await act.run("POST", LINES, { entry: entry.id, account, [side]: amount, description: words, cost_centre: centre }, { done: "Line added" });
    if (outcome.ok) {
      setAccount(null);
      setAmount("");
      setWords("");
    }
  };

  return (
    <article className="doc">
      <DocHeader back="/accounts/journals" backLabel="Journal entries" title="New journal entry"
        number={entry ? entry.reference || `Entry ${entry.id}` : undefined}
        state={entry ? (entry.reverses ? "Reversal" : entry.posted ? "Posted" : "Draft") : undefined}
        tone={entry?.posted ? "done" : "draft"}>
        {editable && (draft.dirty || isNew) && <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Start entry" : "Save"}</ActionButton>}
        {entry && !entry.posted && !draft.dirty && can("accounting.post_journalentry") && (
          <ActionButton primary pending={act.pending} disabled={entry.lines.length < 2 || positive(out) || positive(minus("0", out))}
            onClick={() => void act.run("POST", `${ENDPOINT}${entry.id}/post_entry/`, {}, { done: "Posted" })}>Post</ActionButton>
        )}
        {entry?.posted && !entry.reverses && !entry.posted_by && can("accounting.post_journalentry") && (
          <ActionButton danger pending={act.pending} onClick={async () => {
            const memo = window.prompt("Why is it reversed?");
            if (memo === null) return;
            const outcome = await act.run("POST", `${ENDPOINT}${entry.id}/reverse/`, { memo }, { done: "Reversed" });
            if (outcome.ok) navigate(`/accounts/journals/${outcome.data.id}`);
          }}>Reverse</ActionButton>
        )}
      </DocHeader>

      <Sheet>
        <div className="field-grid">
          <Field label="Date" errors={draft.errors.date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.date ?? "")} onChange={(e) => draft.set("date", e.target.value as never)} />
              : <output id={fid}>{date(entry?.date)}</output>}
          </Field>
          <Field label="Reference" errors={draft.errors.reference}>
            {(fid) => editable
              ? <input id={fid} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} />
              : <output id={fid}>{entry?.reference || "—"}</output>}
          </Field>
          <Field label="What it is for" errors={draft.errors.memo} wide>
            {(fid) => editable
              ? <input id={fid} value={String(value.memo ?? "")} onChange={(e) => draft.set("memo", e.target.value as never)} />
              : <output id={fid}>{entry?.memo || "—"}</output>}
          </Field>
          {entry?.posted_by && <Field label="Posted by" hint="Corrected there: a credit note, a void, a return">{(fid) => <output id={fid}>{entry.posted_by}</output>}</Field>}
          {entry?.reverses && <Field label="Reverses">{(fid) => <output id={fid}><Link to={`/accounts/journals/${entry.reverses}`}>The original entry</Link></output>}</Field>}
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}

        {entry && (
          <div className="lines">
            <table>
              <thead><tr><th scope="col">Account</th><th scope="col">Party</th><th scope="col">Line</th><th scope="col">Centre</th><th scope="col" className="k-money">Debit</th><th scope="col" className="k-money">Credit</th>{editable && <th />}</tr></thead>
              <tbody>
                {entry.lines.map((line) => (
                  <tr key={line.id}>
                    <td><Link to={`/accounts/chart/${line.account}`}>{line.account_code}</Link> {line.account_name}</td>
                    <td>{line.party_name}</td>
                    <td>{line.description}</td>
                    <td>{line.cost_centre_name || "—"}</td>
                    <td className="k-money">{positive(line.debit) ? money(line.debit) : ""}</td>
                    <td className="k-money">{positive(line.credit) ? money(line.credit) : ""}</td>
                    {editable && (
                      <td><button type="button" className="icon-btn" aria-label={`Remove the ${line.account_code} line`} disabled={act.pending}
                        onClick={() => void act.run("DELETE", `${LINES}${line.id}/`, undefined, { done: "Line removed" })}>×</button></td>
                    )}
                  </tr>
                ))}
                {entry.lines.length === 0 && <tr><td colSpan={6} className="empty-line">No lines yet.</td></tr>}
              </tbody>
            </table>
            {editable && (
              <form className="add-line" onSubmit={(event) => { event.preventDefault(); void addLine(); }}>
                <RecordPicker<Account> endpoint="/api/accounting/accounts/" value={account} onChange={(next) => setAccount(next)}
                  label={(row) => `${row.code} · ${row.name}`} fixed={{ is_active: "true" }} placeholder="Account: code or name" ariaLabel="Account" />
                <select aria-label="Debit or credit" value={side} onChange={(e) => setSide(e.target.value as "debit" | "credit")}>
                  <option value="debit">Debit</option><option value="credit">Credit</option>
                </select>
                <DecimalInput places={2} value={amount} onChange={setAmount} aria-label="Amount" className="qty" />
                <input aria-label="Line description" placeholder="Line description" value={words} onChange={(e) => setWords(e.target.value)} />
                {can("accounting.view_costcentre") && (
                  <RecordPicker<Centre> endpoint="/api/accounting/cost-centres/" value={centre} onChange={(next) => setCentre(next)}
                    label={(row) => `${row.code} · ${row.name}`} fixed={{ is_active: "true" }} placeholder="Cost centre" ariaLabel="Cost centre" />
                )}
                <button type="submit" className="btn" disabled={!account || !positive(amount || "0") || act.pending}>Add line</button>
              </form>
            )}
            <Totals rows={[["Debits", debits], ["Credits", credits], ["Out of balance", out, true]]} />
          </div>
        )}
      </Sheet>
      {entry && <Trail model="accounting.journalentry" id={entry.id} />}
    </article>
  );
}
