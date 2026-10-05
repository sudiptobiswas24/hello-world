import { Link, useNavigate, useParams } from "react-router";

import { useAct, useRecord, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, type TradeLine } from "../../forms/Lines";
import { PartyPicker } from "../../forms/PartyPicker";
import { useDraft } from "../../forms/useDraft";
import { minus, positive } from "../../lib/decimal";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { billState } from "./Bills";

interface Bill {
  id: number;
  number: string;
  vendor: number | null;
  vendor_name: string;
  bill_date: string;
  due_date: string | null;
  reference: string;
  purchase_order: number | null;
  debits: number | null;
  posted: boolean;
  is_prepayment: boolean;
  lines: TradeLine[];
  subtotal: string;
  tax_total: string;
  total: string;
  amount_paid: string;
  amount_debited: string;
  amount_due: string;
  settlement_status: string;
  [key: string]: unknown;
}

interface Allocation {
  id: number;
  bill: number;
  payment: number;
  payment_number: string;
  amount: string;
}

const ENDPOINT = "/api/purchasing/bills/";

/**
 * A vendor's bill or a debit note against one: the mirror of an invoice.
 * A draft is checked against what was ordered and received and posted;
 * what a posted bill charged is corrected by a debit note, never edited.
 */
export default function BillForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Bill>(ENDPOINT, id);
  const bill = record.data;
  const draft = useDraft<Bill>(isNew ? ({ vendor: null, bill_date: today(), reference: "" } as unknown as Bill) : bill);
  const act = useAct<Bill>();
  const seesPayments = can("purchasing.view_billpayment");
  const allocations = useRows<Allocation>("/api/purchasing/bill-payments/", { bill: bill?.id }, Boolean(bill?.posted) && seesPayments);

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !bill) return <div className="loading">Opening…</div>;

  const editable = isNew || (!bill!.posted && can("purchasing.change_bill"));
  const value = draft.value;
  const state = bill ? billState(bill) : undefined;
  const title = bill?.debits ? "Debit note" : "Bill";

  const save = async () => {
    if (isNew) {
      const outcome = await act.run("POST", ENDPOINT, {
        vendor: value.vendor, bill_date: value.bill_date, reference: value.reference,
      }, { done: "Bill created" });
      if (outcome.ok) {
        draft.reset();
        navigate(`/purchasing/bills/${outcome.data.id}`, { replace: true });
      } else draft.failed(outcome.error);
      return;
    }
    const outcome = await act.run("PATCH", `${ENDPOINT}${bill!.id}/`, draft.changes, { done: "Saved" });
    if (outcome.ok) draft.reset();
    else draft.failed(outcome.error);
  };

  const post = () => act.run("POST", `${ENDPOINT}${bill!.id}/post_bill/`, {}, {
    done: (result) => `${(result as Bill).number} posted`,
  });
  const debit = async () => {
    if (!window.confirm(`Debit all of ${bill!.number}? A debit note is posted at once.`)) return;
    const outcome = await act.run("POST", `${ENDPOINT}${bill!.id}/debit_note/`, {}, {
      done: (result) => `Debit note ${(result as Bill).number} posted`,
    });
    if (outcome.ok) navigate(`/purchasing/bills/${outcome.data.id}`);
  };

  return (
    <article className="doc">
      <DocHeader back="/purchasing/bills" backLabel="Bills" title={isNew ? "New bill" : title}
        number={bill?.number || (bill ? `Draft ${title.toLowerCase()}` : undefined)} state={state?.label} tone={state?.tone}>
        {editable && (draft.dirty || isNew) && (
          <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Create" : "Save"}</ActionButton>
        )}
        {bill && !bill.posted && !draft.dirty && can("purchasing.post_bill") && (
          <ActionButton primary pending={act.pending} disabled={bill.lines.length === 0} onClick={() => void post()}>Post</ActionButton>
        )}
        {bill?.posted && !bill.debits && can("purchasing.post_bill") && positive(minus(bill.total, bill.amount_debited)) && (
          <ActionButton pending={act.pending} onClick={() => void debit()}>Debit note</ActionButton>
        )}
        {bill?.posted && !bill.debits && positive(bill.amount_due) && can("accounting.add_payment") && (
          <Link className="btn" to={`/purchasing/payments/new?vendor=${bill.vendor}&bill=${bill.id}`}>Pay</Link>
        )}
      </DocHeader>

      <Sheet>
        <div className="field-grid">
          <Field label="Vendor" errors={draft.errors.vendor}>
            {(fid) => editable
              ? <PartyPicker role="vendor" id={fid} value={(value.vendor as number | null) ?? null} invalid={!!draft.errors.vendor} onChange={(v) => draft.set("vendor", v as never)} />
              : <output id={fid}>{bill?.vendor_name}</output>}
          </Field>
          <Field label="Date" errors={draft.errors.bill_date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.bill_date ?? "")} onChange={(e) => draft.set("bill_date", e.target.value as never)} />
              : <output id={fid}>{date(bill?.bill_date)}</output>}
          </Field>
          <Field label="Due" errors={[]}>
            {(fid) => <output id={fid}>{bill?.due_date ? date(bill.due_date) : "Set when posted"}</output>}
          </Field>
          <Field label="Their invoice number" hint="As printed on the vendor's invoice" errors={draft.errors.reference}>
            {(fid, described) => editable
              ? <input id={fid} aria-describedby={described} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} />
              : <output id={fid}>{bill?.reference || "—"}</output>}
          </Field>
          {bill?.purchase_order && (
            <Field label="From order">{(fid) => <output id={fid}><Link to={`/purchasing/orders/${bill.purchase_order}`}>Open the order</Link></output>}</Field>
          )}
          {bill?.debits && (
            <Field label="Debits">{(fid) => <output id={fid}><Link to={`/purchasing/bills/${bill.debits}`}>Open the bill</Link></output>}</Field>
          )}
        </div>
        {["payable_account", "non_field_errors"].map((key) => draft.errors[key] && (
          <p key={key} className="form-error" role="alert">{draft.errors[key]!.join(" ")}</p>
        ))}

        {bill && (
          <>
            <Lines lines={bill.lines} endpoint="/api/purchasing/bill-lines/" parent="bill" parentId={bill.id} side="purchase"
              editable={!bill.posted && can("purchasing.change_bill")} />
            <Totals rows={[
              ["Untaxed", bill.subtotal], ["Tax", bill.tax_total], ["Total", bill.total, true],
              ...(bill.posted && !bill.debits ? [
                ["Paid", bill.amount_paid] as [string, string],
                ["Debited", bill.amount_debited] as [string, string],
                ["Due now", bill.amount_due, true] as [string, string, boolean],
              ] : []),
            ]} />
          </>
        )}
        {isNew && <p className="muted">Create the bill, then add its lines. A bill for an order is raised from the order, so it is checked against what arrived.</p>}
      </Sheet>

      {bill?.posted && seesPayments && (
        <section className="related-list">
          <h2>Payments applied</h2>
          {allocations.data?.length ? (
            <ul>
              {allocations.data.map((row) => (
                <li key={row.id}>
                  {can("accounting.view_payment")
                    ? <Link to={`/purchasing/payments/${row.payment}`}>{row.payment_number || "Payment"}</Link>
                    : <span>{row.payment_number || "Payment"}</span>}
                  <span>{money(row.amount)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted">{allocations.isPending ? "…" : "None yet."}</p>}
        </section>
      )}
    </article>
  );
}
