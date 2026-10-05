import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { useAct, useRecord, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { DecimalInput, Field, today } from "../../forms/fields";
import { useDraft } from "../../forms/useDraft";
import { least, minus, positive, sum } from "../../lib/decimal";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { CustomerPicker } from "./OrderForm";

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
  [key: string]: unknown;
}

interface Allocation {
  id: number;
  invoice: number;
  invoice_number: string;
  payment_number: string;
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

/**
 * Money a customer paid: recorded, posted to the bank, then applied to
 * the invoices it settles. What is applied can be moved later; what was
 * received cannot be edited once posted, only voided (a bounced cheque),
 * which puts the invoices back to owing.
 */
export default function ReceiptForm() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Payment>(ENDPOINT, id);
  const payment = record.data;
  const presetCustomer = Number(params.get("customer")) || null;
  const draft = useDraft<Payment>(isNew ? ({ party: presetCustomer, payment_date: today(), amount: "", reference: "" } as unknown as Payment) : payment);
  const act = useAct<Payment>();

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !payment) return <div className="loading">Opening…</div>;

  const value = draft.value;
  const editable = isNew || (!payment!.posted && can("accounting.change_payment"));

  const record_ = async (andPost: boolean) => {
    const outcome = isNew
      ? await act.run("POST", ENDPOINT, {
          party: value.party, direction: "receipt", payment_date: value.payment_date,
          amount: value.amount, reference: value.reference,
        }, { done: "Receipt recorded" })
      : await act.run("PATCH", `${ENDPOINT}${payment!.id}/`, draft.changes, { done: "Saved" });
    if (!outcome.ok) {
      draft.failed(outcome.error);
      return;
    }
    draft.reset();
    const saved = outcome.data;
    if (andPost) await act.run("POST", `${ENDPOINT}${saved.id}/post_payment/`, {}, { done: `${saved.number || "Receipt"} posted to the bank` });
    if (isNew) {
      const invoice = params.get("invoice");
      navigate(`/sales/receipts/${saved.id}${invoice ? `?invoice=${invoice}` : ""}`, { replace: true });
    }
  };

  const state = payment ? (payment.voided ? "Void" : payment.posted ? "Posted" : "Draft") : undefined;
  return (
    <article className="doc">
      <DocHeader back="/sales/receipts" backLabel="Money received" title="New receipt" number={payment?.number || (payment ? "Draft receipt" : undefined)}
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
        {payment?.posted && !payment.voided && can("accounting.post_payment") && (
          <ActionButton danger pending={act.pending} onClick={() => {
            const memo = window.prompt("Why is it void? (a bounced cheque, a recalled transfer)");
            if (memo !== null) void act.run("POST", `${ENDPOINT}${payment.id}/void/`, { memo }, { done: "Voided: the invoices it paid are owed again" });
          }}>Void</ActionButton>
        )}
      </DocHeader>
      <Sheet>
        <div className="field-grid">
          <Field label="Customer" errors={draft.errors.party}>
            {(fid) => editable
              ? <CustomerPicker id={fid} value={(value.party as number | null) ?? null} invalid={!!draft.errors.party} onChange={(v) => draft.set("party", v as never)} />
              : <output id={fid}>{payment?.party_name}</output>}
          </Field>
          <Field label="Received on" errors={draft.errors.payment_date}>
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
        </div>
        {["bank_account", "counterpart_account", "non_field_errors"].map((key) => draft.errors[key] && (
          <p key={key} className="form-error" role="alert">{draft.errors[key]!.join(" ")}</p>
        ))}
      </Sheet>
      {payment?.posted && !payment.voided && can("sales.view_invoicepayment") && can("sales.view_invoice") && (
        <Apply payment={payment} highlight={Number(params.get("invoice")) || null} />
      )}
    </article>
  );
}

/** Putting a posted receipt against the invoices it pays. */
function Apply({ payment, highlight }: { payment: Payment; highlight: number | null }) {
  const { can } = useAccess();
  const act = useAct();
  const applied = useRows<Allocation>("/api/sales/invoice-payments/", { payment: payment.id });
  const open = useRows<OpenInvoice>("/api/sales/invoices/", { customer: payment.party, open: "true", ordering: "due_date" }, payment.party !== null);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const left = minus(payment.amount, sum((applied.data ?? []).map((row) => row.amount)));
  const canApply = can("sales.add_invoicepayment");

  return (
    <section className="sheet apply">
      <header className="apply-head">
        <h2>Applied to invoices</h2>
        <span className={positive(left) ? "pill pill-open" : "pill pill-done"}>{positive(left) ? `${money(left)} not yet applied` : "All applied"}</span>
      </header>
      {applied.data?.length ? (
        <table>
          <tbody>
            {applied.data.map((row) => (
              <tr key={row.id}>
                <td><Link to={`/sales/invoices/${row.invoice}`}>{row.invoice_number}</Link></td>
                <td className="k-money">{money(row.amount)}</td>
                <td>{can("sales.delete_invoicepayment") && (
                  <button type="button" className="icon-btn" aria-label={`Take ${row.invoice_number} off this receipt`} disabled={act.pending}
                    onClick={() => void act.run("DELETE", `/api/sales/invoice-payments/${row.id}/`, undefined, { done: "Taken off" })}>×</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted">Nothing applied yet.</p>}

      {canApply && positive(left) && (
        <>
          <h3>Open invoices of this customer</h3>
          {open.data?.length ? (
            <table>
              <thead><tr><th scope="col">Invoice</th><th scope="col">Due</th><th scope="col" className="k-money">Owed</th><th scope="col" className="k-money">Apply</th><th /></tr></thead>
              <tbody>
                {open.data.map((invoice) => {
                  const suggested = least(invoice.amount_due, left);
                  const typed = amounts[invoice.id] ?? suggested;
                  return (
                    <tr key={invoice.id} className={invoice.id === highlight ? "highlight" : undefined}>
                      <td><Link to={`/sales/invoices/${invoice.id}`}>{invoice.number}</Link></td>
                      <td>{date(invoice.due_date)}</td>
                      <td className="k-money">{money(invoice.amount_due)}</td>
                      <td className="k-money">
                        <DecimalInput className="cell-input" places={2} aria-label={`Amount to apply to ${invoice.number}`} value={typed}
                          onChange={(v) => setAmounts({ ...amounts, [invoice.id]: v })} />
                      </td>
                      <td>
                        <button type="button" className="btn" disabled={act.pending || !positive(typed || "0")}
                          onClick={() => void act.run("POST", "/api/sales/invoice-payments/", { invoice: invoice.id, payment: payment.id, amount: typed },
                            { done: `Applied to ${invoice.number}`, onDone: () => setAmounts({}) })}>
                          Apply
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <p className="muted">{open.isPending ? "…" : "This customer owes nothing on posted invoices."}</p>}
        </>
      )}
    </section>
  );
}
