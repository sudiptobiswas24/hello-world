import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useAct, useRecord, useReference } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { CommitDecimal, CommitText, DecimalInput, Field } from "../../forms/fields";
import { aboveZero } from "../../lib/decimal";
import { date, quantity } from "../../lib/format";
import { ErrorPanel } from "../../shell/ErrorPanel";
import { RecordPanel } from "../../views/RecordScreen";

import { INSPECTION_PANELS } from "./inspection";
import { Trail } from "../../views/Trail";

interface ReceiptLine {
  id: number;
  order_line: number;
  description: string;
  warehouse: number;
  quantity_received: string;
  lot: number | null;
  lot_code: string;
  tracking: "none" | "lot" | "serial";
}

interface Receipt {
  id: number;
  number: string;
  purchase_order: number;
  order_number: string;
  vendor_name: string;
  receipt_date: string;
  reference: string;
  posted: boolean;
  reverses: number | null;
  lines: ReceiptLine[];
}

interface Warehouse {
  id: number;
  code: string;
}

const ENDPOINT = "/api/purchasing/goods-receipts/";
const LINES = "/api/purchasing/goods-receipt-lines/";

/**
 * What came through the gate: the mirror of a delivery. A draft's
 * quantities are cut to what was actually unloaded and each tracked
 * line is given the batch on its bags, because the receipt is where a
 * batch enters the company; posting puts it on the shelf. A mistake is
 * sent back, all or part, never edited.
 */
export default function GoodsReceiptForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { can } = useAccess();
  const record = useRecord<Receipt>(ENDPOINT, id);
  const warehouses = useReference<Warehouse>("/api/inventory/warehouses/");
  const act = useAct<Receipt>();
  const [returning, setReturning] = useState<Record<number, string> | null>(null);
  const [debit, setDebit] = useState(true);

  if (record.isError) return <ErrorPanel error={record.error} retry={() => void record.refetch()} />;
  const receipt = record.data;
  if (!receipt) return <div className="loading">Opening…</div>;

  const editable = !receipt.posted && can("purchasing.change_goodsreceiptline");
  const where = (wid: number) => warehouses.data?.find((w) => w.id === wid)?.code ?? `#${wid}`;
  const unnamed = receipt.lines.filter((line) => line.tracking !== "none" && !line.lot);
  const state = receipt.reverses ? "Sent back" : receipt.posted ? "Received" : "Draft";

  const post = () => act.run("POST", `${ENDPOINT}${receipt.id}/post_receipt/`, {}, {
    done: (result) => `${(result as Receipt).number} received`,
  });
  const sendBack = async () => {
    const quantities = Object.fromEntries(Object.entries(returning ?? {}).filter(([, q]) => aboveZero(q)));
    if (Object.keys(quantities).length === 0) return;
    const outcome = await act.run("POST", `${ENDPOINT}${receipt.id}/return_receipt/`, {
      quantities, debit_bills: debit,
    }, { done: debit ? "Sent back, and the bill debited" : "Sent back" });
    if (outcome.ok) {
      setReturning(null);
      navigate(`/purchasing/goods-in/${outcome.data.id}`);
    }
  };

  return (
    <article className="doc">
      <DocHeader back="/purchasing/goods-in" backLabel="Goods in" title="Goods receipt" number={receipt.number || "Draft receipt"}
        state={state} tone={receipt.reverses ? "info" : receipt.posted ? "done" : "draft"}>
        {!receipt.posted && can("purchasing.post_goodsreceipt") && (
          <ActionButton primary pending={act.pending} disabled={receipt.lines.length === 0} onClick={() => void post()}>Post receipt</ActionButton>
        )}
        {receipt.posted && !receipt.reverses && can("purchasing.post_goodsreceipt") && returning === null && (
          <ActionButton pending={act.pending} onClick={() => setReturning({})}>Send goods back</ActionButton>
        )}
      </DocHeader>

      {!receipt.posted && unnamed.length > 0 && (
        <div className="note warn" role="note">
          <strong>Name the batch of {unnamed.map((line) => line.description).join(", ")} before posting.</strong>
          <span> It is written on the bags; this is where it enters the company.</span>
        </div>
      )}

      <Sheet>
        <div className="field-grid">
          <Field label="Vendor">{(fid) => <output id={fid}>{receipt.vendor_name}</output>}</Field>
          <Field label="Order">{(fid) => <output id={fid}><Link to={`/purchasing/orders/${receipt.purchase_order}`}>{receipt.order_number || "Open the order"}</Link></output>}</Field>
          <Field label="Date">{(fid) => <output id={fid}>{date(receipt.receipt_date)}</output>}</Field>
          <Field label="Reference">{(fid) => <output id={fid}>{receipt.reference || "—"}</output>}</Field>
          {receipt.reverses && <Field label="Sends back">{(fid) => <output id={fid}><Link to={`/purchasing/goods-in/${receipt.reverses}`}>The original receipt</Link></output>}</Field>}
        </div>

        <div className="lines">
          <table>
            <thead>
              <tr>
                <th scope="col">Goods</th>
                <th scope="col">To</th>
                <th scope="col">Batch</th>
                <th scope="col" className="k-quantity">Quantity</th>
                {returning !== null && <th scope="col" className="k-quantity">Going back</th>}
              </tr>
            </thead>
            <tbody>
              {receipt.lines.map((line) => (
                <tr key={line.id}>
                  <td>{line.description}</td>
                  <td>{where(line.warehouse)}</td>
                  <td>
                    {line.tracking === "none" ? <span className="muted">—</span>
                      : editable ? (
                        <CommitText value={line.lot_code} label={`Batch of ${line.description}`} placeholder="As on the bags"
                          onCommit={(batch) => void act.run("PATCH", `${LINES}${line.id}/`, { batch })} />
                      ) : line.lot_code || "—"}
                  </td>
                  <td className="k-quantity">
                    {editable ? (
                      <CommitDecimal value={line.quantity_received} label={`Quantity of ${line.description}`}
                        onCommit={(q) => void act.run("PATCH", `${LINES}${line.id}/`, { quantity_received: q })} />
                    ) : quantity(line.quantity_received)}
                  </td>
                  {returning !== null && (
                    <td className="k-quantity">
                      <DecimalInput className="cell-input" aria-label={`Quantity of ${line.description} going back`}
                        value={returning[line.id] ?? ""} onChange={(q) => setReturning({ ...returning, [line.id]: q })} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {returning !== null && (
          <div className="return-box">
            <label className="check">
              <input type="checkbox" checked={debit} onChange={(e) => setDebit(e.target.checked)} />
              Debit the vendor's bill for what goes back (clear this when a replacement is coming)
            </label>
            <ActionButton primary pending={act.pending} onClick={() => void sendBack()}>Post the return</ActionButton>
            <button type="button" className="btn" onClick={() => setReturning(null)}>Not now</button>
          </div>
        )}
      </Sheet>
      {receipt.posted && INSPECTION_PANELS.map((panel) => (
        <RecordPanel key={panel.title} panel={panel} record={receipt as unknown as Record<string, unknown> & { id: number }} />
      ))}
      {receipt && <Trail model="purchasing.goodsreceipt" id={receipt.id} />}
    </article>
  );
}
