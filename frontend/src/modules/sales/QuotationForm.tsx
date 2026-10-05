import { Link, useNavigate, useParams } from "react-router";

import { useAct, useRecord } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet, Steps, Totals } from "../../forms/Document";
import { Field, today } from "../../forms/fields";
import { Lines, type TradeLine } from "../../forms/Lines";
import { useDraft } from "../../forms/useDraft";
import { date } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { CustomerPicker } from "./OrderForm";

interface Quotation {
  id: number;
  number: string;
  revision: number;
  revision_of: number | null;
  customer: number | null;
  customer_name: string;
  quotation_date: string;
  valid_until: string | null;
  reference: string;
  status: string;
  sales_order: number | null;
  lines: TradeLine[];
  subtotal: string;
  tax_total: string;
  total: string;
  [key: string]: unknown;
}

const ENDPOINT = "/api/sales/quotations/";
const STAGES: Record<string, number> = { draft: 0, sent: 1, accepted: 2 };
const TONE: Record<string, string> = {
  draft: "draft", sent: "open", accepted: "done", declined: "cancelled", expired: "cancelled", superseded: "info",
};

/**
 * A price offered. Once it has gone to the customer it records what they
 * were told: changing it means a revision, which keeps the old one.
 * Accepting it makes the order.
 */
export default function QuotationForm() {
  const { id } = useParams();
  const isNew = id === "new";
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Quotation>(ENDPOINT, id);
  const quote = record.data;
  const draft = useDraft<Quotation>(isNew ? ({ customer: null, quotation_date: today(), valid_until: "", reference: "" } as unknown as Quotation) : quote);
  const act = useAct<Quotation>();

  if (!isNew && record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  if (!isNew && !quote) return <div className="loading">Opening…</div>;

  const editable = isNew || (quote!.status === "draft" && can("sales.change_quotation"));
  const value = draft.value;

  const save = async () => {
    const body = { customer: value.customer, quotation_date: value.quotation_date, reference: value.reference,
      valid_until: value.valid_until || null };
    const outcome = isNew
      ? await act.run("POST", ENDPOINT, body, { done: "Quotation created" })
      : await act.run("PATCH", `${ENDPOINT}${quote!.id}/`, draft.changes, { done: "Saved" });
    if (outcome.ok) {
      draft.reset();
      if (isNew) navigate(`/sales/quotations/${outcome.data.id}`, { replace: true });
    } else draft.failed(outcome.error);
  };
  const run = async (action: string, done: string, go?: (result: { id: number }) => string) => {
    const outcome = await act.run("POST", `${ENDPOINT}${quote!.id}/${action}/`, {}, { done });
    if (outcome.ok && go) navigate(go(outcome.data as unknown as { id: number }));
  };

  return (
    <article className="doc">
      <DocHeader back="/sales/quotations" backLabel="Quotations" title="New quotation"
        number={quote ? `${quote.number || "Draft quotation"}${quote.revision > 1 ? ` · revision ${quote.revision}` : ""}` : undefined}
        state={quote?.status} tone={quote ? TONE[quote.status] : undefined}>
        {editable && (draft.dirty || isNew) && <ActionButton primary pending={act.pending} onClick={() => void save()}>{isNew ? "Create" : "Save"}</ActionButton>}
        {quote?.status === "draft" && !draft.dirty && can("sales.change_quotation") && (
          <ActionButton primary pending={act.pending} disabled={quote.lines.length === 0} onClick={() => void run("mark_sent", "Marked as sent")}>Mark as sent</ActionButton>
        )}
        {quote && ["draft", "sent"].includes(quote.status) && can("sales.add_salesorder") && (
          <ActionButton primary={quote.status === "sent"} pending={act.pending} disabled={quote.lines.length === 0}
            onClick={() => void run("accept", "Accepted: order created", (order) => `/sales/orders/${order.id}`)}>Accept</ActionButton>
        )}
        {quote?.status === "sent" && can("sales.change_quotation") && (
          <>
            <ActionButton pending={act.pending} onClick={() => void run("revise", "Revision drafted", (rev) => `/sales/quotations/${rev.id}`)}>Revise</ActionButton>
            <ActionButton danger pending={act.pending} onClick={() => void run("decline", "Marked as declined")}>Declined</ActionButton>
          </>
        )}
        {quote && <a className="btn" href={`${ENDPOINT}${quote.id}/pdf/`} target="_blank" rel="noopener">PDF</a>}
      </DocHeader>
      {quote && quote.status in STAGES && <Steps steps={["Draft", "Sent", "Accepted"]} at={STAGES[quote.status]!} />}

      <Sheet>
        <div className="field-grid">
          <Field label="Customer" errors={draft.errors.customer}>
            {(fid) => editable
              ? <CustomerPicker id={fid} value={(value.customer as number | null) ?? null} invalid={!!draft.errors.customer} onChange={(v) => draft.set("customer", v as never)} />
              : <output id={fid}>{quote?.customer_name}</output>}
          </Field>
          <Field label="Date" errors={draft.errors.quotation_date}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.quotation_date ?? "")} onChange={(e) => draft.set("quotation_date", e.target.value as never)} />
              : <output id={fid}>{date(quote?.quotation_date)}</output>}
          </Field>
          <Field label="Valid until" errors={draft.errors.valid_until}>
            {(fid) => editable
              ? <input id={fid} type="date" value={String(value.valid_until ?? "")} onChange={(e) => draft.set("valid_until", e.target.value as never)} />
              : <output id={fid}>{quote?.valid_until ? date(quote.valid_until) : "—"}</output>}
          </Field>
          <Field label="Reference" errors={draft.errors.reference}>
            {(fid) => editable
              ? <input id={fid} value={String(value.reference ?? "")} onChange={(e) => draft.set("reference", e.target.value as never)} />
              : <output id={fid}>{quote?.reference || "—"}</output>}
          </Field>
          {quote?.sales_order && <Field label="Order">{(fid) => <output id={fid}><Link to={`/sales/orders/${quote.sales_order}`}>Open the order</Link></output>}</Field>}
          {quote?.revision_of && <Field label="Revises">{(fid) => <output id={fid}><Link to={`/sales/quotations/${quote.revision_of}`}>The earlier quotation</Link></output>}</Field>}
        </div>
        {draft.errors.non_field_errors && <p className="form-error" role="alert">{draft.errors.non_field_errors.join(" ")}</p>}
        {quote && (
          <>
            <Lines lines={quote.lines} endpoint="/api/sales/quotation-lines/" parent="quotation" parentId={quote.id} withUom
              editable={quote.status === "draft" && can("sales.change_quotation")} />
            <Totals rows={[["Untaxed", quote.subtotal], ["Tax", quote.tax_total], ["Total", quote.total, true]]} />
          </>
        )}
        {isNew && <p className="muted">Create the quotation, then add its lines.</p>}
      </Sheet>
    </article>
  );
}
