import { useState } from "react";
import { useNavigate } from "react-router";

import { useAct, useReference } from "../api/hooks";
import { useAccess } from "../auth/me";
import { DecimalInput } from "./fields";

interface Tax { id: number; code: string; name: string; rate: string }
interface Account { id: number; code: string; name: string }

interface Line {
  description: string;
  quantity: string;
  unit_price: string;
  taxes: number[];
  account: string;
}

const blank = (): Line => ({ description: "", quantity: "1", unit_price: "", taxes: [], account: "" });

/**
 * A note with GST on an invoice or bill the old system issued: here it is
 * only what was owed, with no lines to credit, so the note's lines are
 * typed. It is posted at once and reported against the old number.
 */
export function OldSupplyNote({ endpoint, id, path, permission, title, accountField, accountType,
  askValue, valueRequired = false, valueLabel, href }: {
  endpoint: string;
  id: number;
  path: string;
  permission: string;
  title: string;
  accountField: "revenue_account" | "expense_account";
  accountType: "income" | "expense";
  /** The buyer is unregistered: what the old invoice was for decides where it is reported. */
  askValue: boolean;
  /** Posting waits for it: an unregistered buyer's note is placed by it. */
  valueRequired?: boolean;
  valueLabel: string;
  href: (id: number) => string;
}) {
  const { can } = useAccess();
  const navigate = useNavigate();
  const allowed = can(permission);
  const taxes = useReference<Tax>("/api/accounting/taxes/", undefined, allowed);
  const accounts = useReference<Account>("/api/accounting/accounts/", { account_type: accountType }, allowed);
  const act = useAct<{ id: number; number: string }>();
  const [open, setOpen] = useState(false);
  const [lines, setLines] = useState<Line[]>([blank()]);
  const [value, setValue] = useState("");
  const [memo, setMemo] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  if (!allowed) return null;
  if (!open) {
    return <button type="button" className="btn" onClick={() => setOpen(true)}>{title}</button>;
  }
  const set = (index: number, change: Partial<Line>) =>
    setLines((current) => current.map((line, i) => (i === index ? { ...line, ...change } : line)));
  const send = async () => {
    const outcome = await act.run("POST", `${endpoint}${id}/${path}/`, {
      memo,
      ...(value ? { old_value: value } : {}),
      lines: lines.map((line) => ({
        description: line.description, quantity: line.quantity, unit_price: line.unit_price,
        taxes: line.taxes, ...(line.account ? { [accountField]: Number(line.account) } : {}),
      })),
    }, { done: (note) => `${note.number} posted` });
    if (outcome.ok) navigate(href(outcome.data.id));
    else setErrors(outcome.error.messages);
  };
  return (
    <form className="sheet action-form" aria-label={title} onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <h2>{title}</h2>
      <p className="muted">Its own lines and tax, posted at once, and reported in this month's return against the old number.</p>
      <table className="lines">
        <thead><tr><th>What</th><th className="k-quantity">Quantity</th><th className="k-money">Price</th><th>Tax</th><th>Account</th><th /></tr></thead>
        <tbody>
          {lines.map((line, index) => (
            <tr key={index}>
              <td><input aria-label={`Line ${index + 1} what`} value={line.description} onChange={(e) => set(index, { description: e.target.value })} placeholder="Rate difference on …" /></td>
              <td><DecimalInput aria-label={`Line ${index + 1} quantity`} value={line.quantity} onChange={(v) => set(index, { quantity: v })} /></td>
              <td><DecimalInput aria-label={`Line ${index + 1} price`} places={2} value={line.unit_price} onChange={(v) => set(index, { unit_price: v })} /></td>
              <td>
                <select multiple aria-label={`Line ${index + 1} tax`} value={line.taxes.map(String)}
                  onChange={(e) => set(index, { taxes: Array.from(e.target.selectedOptions, (o) => Number(o.value)) })}>
                  {(taxes.data ?? []).map((tax) => <option key={tax.id} value={tax.id}>{tax.name || tax.code}</option>)}
                </select>
              </td>
              <td>
                <select aria-label={`Line ${index + 1} account`} value={line.account} onChange={(e) => set(index, { account: e.target.value })}>
                  <option value="">{accountField === "revenue_account" ? "The usual sales account" : "Choose…"}</option>
                  {(accounts.data ?? []).map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}
                </select>
              </td>
              <td>{lines.length > 1 && <button type="button" className="icon-btn" aria-label={`Remove line ${index + 1}`} onClick={() => setLines((c) => c.filter((_, i) => i !== index))}>×</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="btn" onClick={() => setLines((c) => [...c, blank()])}>Add a line</button>
      <div className="field-grid">
        {askValue && (
          <label className="field">{valueLabel}
            <DecimalInput places={2} value={value} onChange={setValue} required={valueRequired} />
          </label>
        )}
        <label className="field wide">Why <input value={memo} onChange={(e) => setMemo(e.target.value)} /></label>
      </div>
      {errors.length > 0 && <p className="form-error" role="alert">{errors.join(" ")}</p>}
      <div className="row-actions">
        <button type="submit" className="btn primary" disabled={act.pending || (valueRequired && !value)}>Post it</button>
        <button type="button" className="btn" onClick={() => setOpen(false)}>Not now</button>
      </div>
    </form>
  );
}
