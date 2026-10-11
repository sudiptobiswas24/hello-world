import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { useAct, usePage, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { DecimalInput, Field, today } from "../../forms/fields";
import { Pager } from "../../forms/Pager";
import { useDraft } from "../../forms/useDraft";
import { least, positive } from "../../lib/decimal";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { PartyPicker, type PartyRole } from "../../forms/PartyPicker";
import { RecordPicker } from "../../forms/RecordPicker";
import { Trail } from "../../views/Trail";

interface Payment {
  id: number;
  number: string;
  party: number | null;
  party_name: string;
  payment_date: string;
  amount: string;
  reference: string;
  memo: string;
  posted: boolean;
  voided: boolean;
  bank_account: number | null;
  bank_account_name: string;
  /** What is left to apply, as the server adds it up. */
  unallocated: string;
  [key: string]: unknown;
}

interface Allocation {
  id: number;
  invoice?: number;
  invoice_number?: string;
  bill?: number;
  bill_number?: string;
  payment: number;
  amount: string;
}

interface OpenInvoice {
  id: number;
  number: string;
  invoice_date: string;
  due_date: string | null;
  amount_due: string;
}

const ENDPOINT = "/api/accounting/payments/";
/** Where money is: the server refuses any other account, and a closed one. */
const MONEY = { holds_money: "true", is_active: "true" };

/** Which way the money went, and what it is put against. */
export interface MoneyConfig {
  direction: "receipt" | "disbursement";
  role: PartyRole;
  base: string; // "/sales/receipts"
  plural: string; // "Money received"
  noun: string; // "receipt"
  dateLabel: string; // "Received on"
  /** The documents it settles: invoices, or bills. */
  field: "invoice" | "bill";
  documents: string; // "/api/sales/invoices/"
  documentHref: (id: number) => string;
  allocations: string; // "/api/sales/invoice-payments/"
  /** The permissions on allocations and on the documents, in that app. */
  app: "sales" | "purchasing";
  /** The documents' control account: only those booked where the money was can take it. */
  account: "receivable_account" | "payable_account";
}

/**
 * Money a customer paid, or money paid to a vendor: recorded, posted to
 * the bank, then applied to the invoices or bills it settles. What is
 * applied can be moved later; what was posted cannot be edited, only
 * voided (a bounced cheque), which puts the documents back to owing.
 */
export function PaymentForm({ config }: { config: MoneyConfig }) {
  const { id } = useParams();
  const [params] = useSearchParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Payment>(ENDPOINT, id);
  const payment = record.data;
  const presetParty = Number(params.get(config.role)) || null;
  const draft = useDraft<Payment>(isNew ? ({ party: presetParty, payment_date: today(), amount: "", reference: "" } as unknown as Payment) : payment);
  const act = useAct<Payment>();

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !payment) return <div className="loading">Opening…</div>;

  const value = draft.value;
  const editable = isNew || (!payment!.posted && can("accounting.change_payment"));
  const readsAccounts = can("accounting.view_account");

  const record_ = async (andPost: boolean) => {
    const outcome = isNew
      ? await act.run("POST", ENDPOINT, {
          party: value.party, direction: config.direction, payment_date: value.payment_date,
          amount: value.amount, reference: value.reference,
          // Left out, the company's bank.
          ...(value.bank_account ? { bank_account: value.bank_account } : {}),
        }, { done: `${config.noun[0]!.toUpperCase()}${config.noun.slice(1)} recorded` })
      : await act.run("PATCH", `${ENDPOINT}${payment!.id}/`, draft.changes, { done: "Saved" });
    if (!outcome.ok) {
      draft.failed(outcome.error);
      return;
    }
    draft.reset();
    const saved = outcome.data;
    if (andPost) await act.run("POST", `${ENDPOINT}${saved.id}/post_payment/`, {}, { done: `${saved.number || config.noun} posted to the bank` });
    if (isNew) {
      const document = params.get(config.field);
      navigate(`${config.base}/${saved.id}${document ? `?${config.field}=${document}` : ""}`, { replace: true });
    }
  };

  const state = payment ? (payment.voided ? "Void" : payment.posted ? "Posted" : "Draft") : undefined;
  return (
    <article className="doc">
      <DocHeader back={config.base} backLabel={config.plural} title={`New ${config.noun}`} number={payment?.number || (payment ? `Draft ${config.noun}` : undefined)}
        state={state} tone={payment?.voided ? "cancelled" : payment?.posted ? "done" : "draft"}>
        {editable && (draft.dirty || isNew) && (
          <>
            <ActionButton pending={act.pending} onClick={() => void record_(false)}>{isNew ? "Save as draft" : "Save"}</ActionButton>
            {can("accounting.post_payment") && <ActionButton primary pending={act.pending} onClick={() => void record_(true)}>{isNew ? "Record and post" : "Save and post"}</ActionButton>}
          </>
        )}
        {payment && !payment.posted && !draft.dirty && can("accounting.post_payment") && (
          <ActionButton primary pending={act.pending} onClick={() => void act.run("POST", `${ENDPOINT}${payment.id}/post_payment/`, {}, { done: "Posted to the bank" })}>Post</ActionButton>
        )}
        {payment?.posted && !payment.voided && (
          <>
            <a className="btn" href={`${ENDPOINT}${payment.id}/pdf/`} target="_blank" rel="noopener">{config.direction === "disbursement" ? "Remittance advice" : "Receipt"} PDF</a>
            {can("accounting.change_payment") && (
              <ActionButton pending={act.pending} onClick={() => void act.run("POST", `${ENDPOINT}${payment.id}/send/`, {}, {
                done: (reply) => `${config.direction === "disbursement" ? "Remittance advice" : "Receipt"} sent to ${(reply as unknown as { sent_to: string }).sent_to}`,
              })}>Email {config.direction === "disbursement" ? "remittance advice" : "receipt"}</ActionButton>
            )}
          </>
        )}
        {payment?.posted && !payment.voided && can("accounting.post_payment") && (
          <ActionButton danger pending={act.pending} onClick={() => {
            const memo = window.prompt("Why is it void? (a bounced cheque, a recalled transfer)");
            if (memo === null) return;
            // The day the bank took it back, so that month's statement reconciles.
            const date = window.prompt("On which day? (the day the bank returned it)", today());
            if (date !== null) void act.run("POST", `${ENDPOINT}${payment.id}/void/`, { memo, date }, { done: `Voided: the ${config.field}s it paid are owed again` });
          }}>Void</ActionButton>
        )}
      </DocHeader>
      <Sheet>
        <div className="field-grid">
          <Field label={config.role === "customer" ? "Customer" : "Vendor"} errors={draft.errors.party}>
            {(fid) => editable
              ? <PartyPicker role={config.role} id={fid} value={(value.party as number | null) ?? null} invalid={!!draft.errors.party} onChange={(v) => draft.set("party", v as never)} />
              : <output id={fid}>{payment?.party_name}</output>}
          </Field>
          <Field label={config.dateLabel} errors={draft.errors.payment_date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.payment_date ?? "")} onChange={(e) => draft.set("payment_date", e.target.value as never)} />
              : <output id={fid}>{date(payment?.payment_date)}</output>}
          </Field>
          <Field label="Amount" errors={draft.errors.amount}>
            {(fid) => editable
              ? <DecimalInput id={fid} places={2} value={String(value.amount ?? "")} onChange={(v) => draft.set("amount", v as never)} aria-invalid={!!draft.errors.amount} />
              : <output id={fid} className="figure">{money(payment?.amount)}</output>}
          </Field>
          <Field label="Reference" hint="Cheque or UTR number" errors={draft.errors.reference}>
            {(fid, described) => editable
              ? <input id={fid} aria-describedby={described} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} />
              : <output id={fid}>{payment?.reference || "—"}</output>}
          </Field>
          {readsAccounts && (
            <Field label={config.direction === "receipt" ? "Into" : "From"} hint={editable ? "A bank, cash or card account. Empty: the company's bank" : undefined} errors={draft.errors.bank_account}>
              {(fid) => editable
                ? <RecordPicker<{ id: number; code: string; name: string }> id={fid} endpoint="/api/accounting/accounts/" fixed={MONEY}
                    value={(value.bank_account as number | null) ?? null} onChange={(next) => draft.set("bank_account", next as never)}
                    label={(row) => `${row.code} · ${row.name}`} placeholder="Bank, cash or card" invalid={!!draft.errors.bank_account} />
                : <output id={fid}>{payment?.bank_account_name}</output>}
            </Field>
          )}
        </div>
        {[...(readsAccounts ? [] : ["bank_account"]), "counterpart_account", "non_field_errors"].map((key) => draft.errors[key] && (
          <p key={key} className="form-error" role="alert">{draft.errors[key]!.join(" ")}</p>
        ))}
      </Sheet>
      {payment?.posted && !payment.voided && can(`${config.app}.view_${config.field}payment`) && can(`${config.app}.view_${config.field}`) && (
        <Apply config={config} payment={payment} highlight={Number(params.get(config.field)) || null} />
      )}
      {payment && <Trail model="accounting.payment" id={payment.id} />}
    </article>
  );
}

/** Putting a posted payment against the invoices or bills it settles. */
function Apply({ config, payment, highlight }: { config: MoneyConfig; payment: Payment; highlight: number | null }) {
  const { can } = useAccess();
  const act = useAct();
  const [appliedPage, setAppliedPage] = useState(1);
  const [openPage, setOpenPage] = useState(1);
  const appliedRows = usePage<Allocation>(config.allocations, { payment: payment.id }, appliedPage, 50);
  // Only what this money can settle: the server refuses another control
  // account or currency, so offering those would only invite a refusal.
  const openRows = usePage<OpenInvoice>(config.documents, {
    [config.role]: payment.party, open: "true", ordering: "due_date",
    [config.account]: String(payment.counterpart_account ?? ""), currency: String(payment.currency ?? ""),
  },
    openPage, 50, payment.party !== null);
  const applied = { data: appliedRows.data?.rows };
  const open = { data: openRows.data?.rows, isPending: openRows.isPending };
  const kind = config.field; // "invoice" | "bill"
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  // The server's figure: adding up the allocations on this page alone
  // read a receipt applied to 250 invoices as part applied.
  const left = payment.unallocated;
  const canApply = can(`${config.app}.add_${kind}payment`);

  return (
    <section className="sheet apply">
      <header className="apply-head">
        <h2>Applied to {kind}s</h2>
        <span className={positive(left) ? "pill pill-open" : "pill pill-done"}>{positive(left) ? `${money(left)} not yet applied` : "All applied"}</span>
      </header>
      {applied.data?.length ? (
        <><table>
          <tbody>
            {applied.data.map((row) => {
              const number = row[`${kind}_number`] ?? "";
              return (
                <tr key={row.id}>
                  <td><Link to={config.documentHref(row[kind] as number)}>{number}</Link></td>
                  <td className="k-money">{money(row.amount)}</td>
                  <td>{can(`${config.app}.delete_${kind}payment`) && (
                    <button type="button" className="icon-btn" aria-label={`Take ${number} off this ${config.noun}`} disabled={act.pending}
                      onClick={() => void act.run("DELETE", `${config.allocations}${row.id}/`, undefined, { done: "Taken off" })}>×</button>
                  )}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {appliedRows.data && <Pager page={appliedPage} size={50} total={appliedRows.data.total} onPage={setAppliedPage} />}</>
      ) : <p className="muted">Nothing applied yet.</p>}

      {canApply && positive(left) && (
        <>
          <h3>Open {kind}s of this {config.role}</h3>
          {open.data?.length ? (
            <><table>
              <thead><tr><th scope="col">{kind === "invoice" ? "Invoice" : "Bill"}</th><th scope="col">Due</th><th scope="col" className="k-money">Owed</th><th scope="col" className="k-money">Apply</th><th /></tr></thead>
              <tbody>
                {open.data.map((invoice) => {
                  const suggested = least(invoice.amount_due, left);
                  const typed = amounts[invoice.id] ?? suggested;
                  return (
                    <tr key={invoice.id} className={invoice.id === highlight ? "highlight" : undefined}>
                      <td><Link to={config.documentHref(invoice.id)}>{invoice.number}</Link></td>
                      <td>{date(invoice.due_date)}</td>
                      <td className="k-money">{money(invoice.amount_due)}</td>
                      <td className="k-money">
                        <DecimalInput className="cell-input" places={2} aria-label={`Amount to apply to ${invoice.number}`} value={typed}
                          onChange={(v) => setAmounts({ ...amounts, [invoice.id]: v })} />
                      </td>
                      <td>
                        <button type="button" className="btn" disabled={act.pending || !positive(typed || "0")}
                          onClick={() => void act.run("POST", config.allocations, { [kind]: invoice.id, payment: payment.id, amount: typed },
                            { done: `Applied to ${invoice.number}`, onDone: () => setAmounts({}) })}>
                          Apply
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {openRows.data && <Pager page={openPage} size={50} total={openRows.data.total} onPage={setOpenPage} />}</>
          ) : <p className="muted">{open.isPending ? "…" : `Nothing is owed on posted ${kind}s.`}</p>}
        </>
      )}
    </section>
  );
}
