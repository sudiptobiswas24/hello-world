import { Link, useNavigate, useParams } from "react-router";
import { HistoryPanel } from "../../views/HistoryPanel";

import { useAct, useRecord, useRows } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, type TradeLine } from "../../forms/Lines";
import { PartyPicker } from "../../forms/PartyPicker";
import { OldSupplyNote } from "../../forms/OldSupplyNote";
import { useDraft } from "../../forms/useDraft";
import { minus, positive } from "../../lib/decimal";
import { date, money } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { RecordPanel, type PanelDef } from "../../views/RecordScreen";
import { billState } from "./Bills";

/** A transporter's freight bill, matched to the deliveries it charges for: each is charged for once. */
const CARRIED: PanelDef = {
  title: "Deliveries carried", permission: "sales.view_delivery", endpoint: "", query: () => ({}),
  rows: (bill) => ((bill.carried as Record<string, unknown>[]) ?? []).map((row) => ({ ...row, id: Number(row.id), bill: bill.id })),
  columns: [
    { key: "number", label: "Delivery" },
    { key: "date", label: "Date", kind: "date", width: "8rem" },
    { key: "lr_number", label: "LR", width: "9rem" },
  ],
  adder: {
    label: "Add a delivery", permission: "purchasing.change_bill",
    url: (bill) => `/api/purchasing/bills/${String(bill.id)}/carried/`,
    fields: (bill) => [{ key: "delivery", label: "Delivery", kind: "pick", pick: {
      endpoint: "/api/sales/deliveries/", permission: "sales.view_delivery",
      query: { transporter: String(bill.vendor), freight_charge__isnull: "true", posted: "true" },
      label: (row: Record<string, unknown>) => `${String(row.number)} · ${String(row.customer_name)} · ${String(row.lr_number || "no LR")}` } }],
    body: (values) => values,
    when: (bill) => !bill.debits,
  },
  remover: {
    permission: "purchasing.change_bill",
    url: (row) => `/api/purchasing/bills/${String(row.bill)}/carried/?delivery=${String(row.delivery)}`,
  },
};

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
  is_opening_balance: boolean;
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
    // A prepayment is taken back by amount: part of what is left, or all of it.
    let body: Record<string, string> = {};
    if (bill!.is_prepayment) {
      const amount = window.prompt(`How much of ${bill!.number} to take back? Leave it empty for all that is left. A debit note is posted at once.`);
      if (amount === null) return;
      if (amount.trim()) body = { amount: amount.trim() };
    } else if (!window.confirm(`Debit all of ${bill!.number}? A debit note is posted at once.`)) return;
    const outcome = await act.run("POST", `${ENDPOINT}${bill!.id}/debit_note/`, body, {
      done: (result) => `Debit note ${(result as Bill).number} posted`,
    });
    if (outcome.ok) navigate(`/purchasing/bills/${outcome.data.id}`);
  };

  const deductTds = () => {
    if (!window.confirm(`Deduct TDS on ${bill!.number} under the vendor's section? It is taken off what the bill owes.`)) return;
    void act.run("POST", `${ENDPOINT}${bill!.id}/deduct_tds/`, {}, { done: "TDS deducted" });
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
        {bill?.posted && !bill.debits && !bill.is_prepayment && positive(bill.amount_due) && can("purchasing.add_tdsdeduction") && (
          <ActionButton pending={act.pending} onClick={deductTds}>Deduct TDS</ActionButton>
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
        {bill?.is_opening_balance && (
          <p className="note" role="note">Brought in from the old system: {bill.reference}, what was still owing on it. A debit note with GST on it, for short weight or a rate difference, takes the input tax back; the Debit note button only clears the balance.</p>
        )}
      </Sheet>

      {bill?.posted && bill.is_opening_balance && (
        <OldSupplyNote endpoint={ENDPOINT} id={bill.id} path="debit_old_supply" permission="purchasing.post_bill"
          title="Debit note with GST" accountField="expense_account" accountType="expense"
          askValue valueLabel="What the old bill was for in all, if known"
          href={(id) => `/purchasing/bills/${id}`} />
      )}

      {bill && !bill.debits && (bill.lines.some((line) => line.charge) || ((bill as unknown as { carried?: unknown[] }).carried ?? []).length > 0) && (
        <RecordPanel panel={CARRIED} record={bill as unknown as Record<string, unknown> & { id: number }} />
      )}

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
      {bill && <HistoryPanel model="purchasing.bill" id={bill.id} />}
    </article>
  );
}
