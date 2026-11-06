import { Link, useNavigate, useParams } from "react-router";
import { Trail } from "../../views/Trail";

import { useAct, useRecord, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ExtraFields } from "../../forms/ExtraFields";
import { useAsk } from "../../forms/Ask";
import { ActionButton, DocHeader, Sheet, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, NewLines, lineBody, type NewLine, type TradeLine } from "../../forms/Lines";
import { OldSupplyNote } from "../../forms/OldSupplyNote";
import { useDraft } from "../../forms/useDraft";
import { minus, positive } from "../../lib/decimal";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { CustomerPicker } from "./OrderForm";

export interface Invoice {
  not_sendable?: string;
  id: number;
  number: string;
  customer: number | null;
  customer_name: string;
  invoice_date: string;
  due_date: string | null;
  reference: string;
  sales_order: number | null;
  credits: number | null;
  posted: boolean;
  is_down_payment: boolean;
  is_opening_balance: boolean;
  party_gstin: string;
  lines: TradeLine[];
  subtotal: string;
  tax_total: string;
  total: string;
  amount_paid: string;
  amount_credited: string;
  amount_due: string;
  settlement_status: string;
  sent_at: string | null;
  [key: string]: unknown;
}

interface Allocation {
  id: number;
  invoice: number;
  payment: number;
  payment_number: string;
  amount: string;
}

const ENDPOINT = "/api/sales/invoices/";

export function invoiceState(invoice: Invoice): { label: string; tone: string } {
  if (!invoice.posted) return { label: "Draft", tone: "draft" };
  if (invoice.credits) return { label: "Credit note", tone: "info" };
  if (invoice.settlement_status === "paid") return { label: "Paid", tone: "done" };
  if (invoice.settlement_status === "partial") return { label: "Part paid", tone: "warn" };
  return { label: "Unpaid", tone: "open" };
}

/**
 * An invoice or a credit note. A draft can be changed and posted; once
 * posted it is a fact: what it billed is corrected by a credit note,
 * never by an edit, and the server refuses one.
 */
export default function InvoiceForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Invoice>(ENDPOINT, id);
  const invoice = record.data;
  const draft = useDraft<Invoice>(isNew ? ({ customer: null, invoice_date: today(), reference: "" } as unknown as Invoice) : invoice);
  const act = useAct<Invoice>();
  const { ask, dialog } = useAsk();
  const seesPayments = can("sales.view_invoicepayment");
  const allocations = useRows<Allocation>("/api/sales/invoice-payments/", { invoice: invoice?.id }, Boolean(invoice?.posted) && seesPayments);

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !invoice) return <div className="loading">Opening…</div>;

  const editable = isNew || (!invoice!.posted && can("sales.change_invoice"));
  const value = draft.value;
  const newLines = ((value as Record<string, unknown>).new_lines as NewLine[] | undefined) ?? [];
  const state = invoice ? invoiceState(invoice) : undefined;
  const title = invoice?.credits ? "Credit note" : "Invoice";

  const save = async () => {
    if (isNew) {
      const outcome = await act.run("POST", ENDPOINT, {
        customer: value.customer, invoice_date: value.invoice_date, reference: value.reference,
        new_lines: newLines.map(lineBody),
      }, { done: "Invoice created" });
      if (outcome.ok) {
        draft.reset();
        navigate(`/sales/invoices/${outcome.data.id}`, { replace: true });
      } else draft.failed(outcome.error);
      return;
    }
    const outcome = await act.run("PATCH", `${ENDPOINT}${invoice!.id}/`, draft.changes, { done: "Saved" });
    if (outcome.ok) draft.reset();
    else draft.failed(outcome.error);
  };

  const post = () => act.run("POST", `${ENDPOINT}${invoice!.id}/post_invoice/`, {}, {
    done: (result) => `${(result as Invoice).number} posted`,
  });
  const credit = async () => {
    // A deposit is given back by amount: part of what is left, or all of it.
    let body: Record<string, string> = {};
    if (invoice!.is_down_payment) {
      const said = await ask({ title: `Credit note on ${invoice!.number}`, submit: "Post the credit note",
        note: "A credit note is posted at once.",
        fields: [{ key: "amount", label: "Amount to give back", kind: "money", hint: "Leave it empty for all that is left" }] });
      if (said === null) return;
      if (said.amount) body = { amount: said.amount };
    } else if (!window.confirm(`Credit all of ${invoice!.number}? A credit note is posted at once.`)) return;
    const outcome = await act.run("POST", `${ENDPOINT}${invoice!.id}/credit_note/`, body, {
      done: (result) => `Credit note ${(result as Invoice).number} posted`,
    });
    if (outcome.ok) navigate(`/sales/invoices/${outcome.data.id}`);
  };
  const writeOff = async () => {
    // Not a credit note: the sale happened and the money never came.
    const said = await ask({ title: `Write off ${invoice!.number}`, submit: "Write off", danger: true, fields: [
      { key: "amount", label: "Amount to write off", kind: "money", hint: "Leave it empty for all that is owed" },
      { key: "reason", label: "Why it will not be paid" },
    ] });
    if (said === null) return;
    await act.run("POST", `${ENDPOINT}${invoice!.id}/write_off/`, { reason: said.reason, ...(said.amount ? { amount: said.amount } : {}) }, {
      done: "Written off to bad debt",
    });
  };
  const email = () => act.run("POST", `${ENDPOINT}${invoice!.id}/send/`, {}, {
    done: (result) => `Sent to ${(result as unknown as { sent_to: string }).sent_to}`,
  });

  return (
    <article className="doc">
      <DocHeader back="/sales/invoices" backLabel="Invoices" title={isNew ? "New invoice" : title}
        number={invoice?.number || (invoice ? `Draft ${title.toLowerCase()}` : undefined)} state={state?.label} tone={state?.tone}>
        {editable && (draft.dirty || isNew) && (
          <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Create" : "Save"}</ActionButton>
        )}
        {invoice && !invoice.posted && !draft.dirty && can("sales.post_invoice") && (
          <ActionButton primary pending={act.pending} disabled={invoice.lines.length === 0} onClick={() => void post()}>Post</ActionButton>
        )}
        {invoice?.posted && !invoice.credits && can("sales.post_invoice") && positive(minus(invoice.total, invoice.amount_credited)) && (
          <ActionButton pending={act.pending} onClick={() => void credit()}>Credit note</ActionButton>
        )}
        {invoice?.posted && !invoice.credits && positive(invoice.amount_due) && can("sales.write_off_invoice") && (
          <ActionButton pending={act.pending} onClick={() => void writeOff()}>Write off</ActionButton>
        )}
        {invoice?.posted && !invoice.credits && positive(invoice.amount_due) && can("accounting.add_payment") && (
          <Link className="btn" to={`/sales/receipts/new?customer=${invoice.customer}&invoice=${invoice.id}`}>Receive payment</Link>
        )}
        {invoice && (
          <a className="btn" href={`${ENDPOINT}${invoice.id}/pdf/`} target="_blank" rel="noopener">PDF</a>
        )}
        {invoice?.posted && !invoice.not_sendable && can("sales.change_invoice") && (
          <ActionButton pending={act.pending} onClick={() => void email()}>{invoice.sent_at ? "Email again" : "Email"}</ActionButton>
        )}
      </DocHeader>
      {dialog}

      <Sheet>
        <div className="field-grid">
          <Field label="Customer" errors={draft.errors.customer}>
            {(fid) => editable
              ? <CustomerPicker id={fid} value={(value.customer as number | null) ?? null} invalid={!!draft.errors.customer} onChange={(v) => draft.set("customer", v as never)} />
              : <output id={fid}>{invoice?.customer_name}</output>}
          </Field>
          <Field label="Date" errors={draft.errors.invoice_date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.invoice_date ?? "")} onChange={(e) => draft.set("invoice_date", e.target.value as never)} />
              : <output id={fid}>{date(invoice?.invoice_date)}</output>}
          </Field>
          <Field label="Due" errors={[]}>
            {(fid) => <output id={fid}>{invoice?.due_date ? date(invoice.due_date) : "Set when posted"}</output>}
          </Field>
          <Field label="Reference" errors={draft.errors.reference}>
            {(fid) => editable
              ? <input id={fid} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} />
              : <output id={fid}>{invoice?.reference || "—"}</output>}
          </Field>
          {invoice?.sales_order && (
            <Field label="From order">{(fid) => <output id={fid}><Link to={`/sales/orders/${invoice.sales_order}`}>Open the order</Link></output>}</Field>
          )}
          {invoice?.credits && (
            <Field label="Credits">{(fid) => <output id={fid}><Link to={`/sales/invoices/${invoice.credits}`}>Open the invoice</Link></output>}</Field>
          )}
        </div>
        <ExtraFields kind="sales.invoice" value={value.extra as Record<string, unknown> | undefined} set={(next) => draft.set("extra", next as never)} errors={draft.errors} editable={editable} />
        {draft.errors.receivable_account && <p className="form-error" role="alert">{draft.errors.receivable_account.join(" ")}</p>}
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}

        {invoice && (
          <>
            <Lines lines={invoice.lines} endpoint="/api/sales/invoice-lines/" parent="invoice" parentId={invoice.id}
              editable={!invoice.posted && can("sales.change_invoice")} />
            <Totals rows={[
              ["Untaxed", invoice.subtotal], ["Tax", invoice.tax_total], ["Total", invoice.total, true],
              ...(invoice.posted && !invoice.credits ? [
                ["Paid", invoice.amount_paid] as [string, string],
                ["Credited", invoice.amount_credited] as [string, string],
                ["Due now", invoice.amount_due, true] as [string, string, boolean],
              ] : []),
            ]} />
          </>
        )}
        {isNew && (
          <>
            <NewLines lines={newLines} onChange={(next) => draft.set("new_lines" as never, next as never)} errors={draft.errors}  />
            <p className="muted">Add its lines, then create it: the invoice and its lines are saved together. An invoice for an order is raised from the order.</p>
          </>
        )}
        {invoice?.is_opening_balance && (
          <p className="note" role="note">Brought in from the old system: {invoice.reference}, what was still owed on it. A rate difference or a return on it is a credit note with its own GST, below; the Credit note button only clears the balance.</p>
        )}
      </Sheet>

      {invoice?.posted && invoice.is_opening_balance && (
        <OldSupplyNote endpoint={ENDPOINT} id={invoice.id} path="credit_old_supply" permission="sales.post_invoice"
          title="Credit note with GST" accountField="revenue_account" accountType="income"
          askValue={!invoice.party_gstin} valueRequired={!invoice.party_gstin}
          valueLabel="What the old invoice was for in all"
          href={(id) => `/sales/invoices/${id}`} />
      )}

      {invoice?.posted && seesPayments && (
        <section className="related-list">
          <h2>Payments applied</h2>
          {allocations.data?.length ? (
            <ul>
              {allocations.data.map((row) => (
                <li key={row.id}>
                  {can("accounting.view_payment")
                    ? <Link to={`/sales/receipts/${row.payment}`}>{row.payment_number || "Receipt"}</Link>
                    : <span>{row.payment_number || "Receipt"}</span>}
                  <span>{money(row.amount)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted">{allocations.isPending ? "…" : "None yet."}</p>}
        </section>
      )}
      {invoice && <Trail model="sales.invoice" id={invoice.id} />}
    </article>
  );
}
