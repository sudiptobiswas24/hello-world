/**
 * View-model for the Quote screen — a straight port of the design's "quote"
 * section of renderVals() and the quote values in its final return.
 */
import { TYPE_CONFIG, fmt, inr, num } from '../../engine';
import type { AppState, CalcResult } from '../../engine';
import { dateLabel } from '../jobcard/model';

export interface QuoteLine {
  id: string;
  checked: boolean;
  name: string;
  qtyS: string;
  rateS: string;
}

export interface QuoteRow {
  id: string;
  i: number;
  name: string;
  desc: string;
  qtyS: string;
  rateS: string;
  amtS: string;
}

export interface QuoteView {
  qNo: string;
  qDate: string;
  qCustomerShow: string;
  qGstinShow: string;
  qLines: QuoteLine[];
  quoteRows: QuoteRow[];
  qSubS: string;
  qGstS: string;
  qTotS: string;
  qGstPctS: string;
}

export function quoteView(state: AppState, results: Record<string, CalcResult>, today: Date): QuoteView {
  const quote = state.quote || { customer: '', gstin: '', sel: {} };
  const qSel = quote.sel || {};
  const qLines: QuoteLine[] = state.skus.map((s) => ({
    id: s.id,
    checked: !!qSel[s.id],
    name: s.name,
    qtyS: fmt(num(s.qty), 0),
    rateS: inr(results[s.id].quote),
  }));

  let qSub = 0;
  const quoteRows: QuoteRow[] = state.skus
    .filter((s) => qSel[s.id])
    .map((s, i) => {
      const r = results[s.id];
      const rate = Math.round(r.quote * 100) / 100;
      const amt = rate * r.qty;
      qSub += amt;
      const bits = [
        (TYPE_CONFIG[s.type] || TYPE_CONFIG.plain).label,
        fmt(num(s.width), 1) + ' × ' + fmt(num(s.length), 1) + ' ' + s.unit,
        'mesh ' + s.warpMesh + '×' + s.weftMesh,
        fmt(r.gsm, 0) + ' GSM',
        fmt(r.totalG, 0) + ' g',
      ];
      if (r.hasLam) bits.push('lam ' + s.lamSide);
      if (r.hasBopp) bits.push(s.boppVariant + ' BOPP ' + s.boppMic + 'µ');
      if (r.printed) bits.push(s.colors + ' colors');
      return { id: s.id, i: i + 1, name: s.name, desc: bits.join(' · '), qtyS: fmt(r.qty, 0), rateS: inr(rate), amtS: inr(amt, 0) };
    });

  const gstPct = num(state.glob.gst);
  const qGst = (qSub * gstPct) / 100;

  return {
    qNo: 'QT-' + (2800 + state.skus.length * 7),
    qDate: dateLabel(today),
    qCustomerShow: quote.customer || '—',
    qGstinShow: quote.gstin || '',
    qLines,
    quoteRows,
    qSubS: inr(qSub, 0),
    qGstS: inr(qGst, 0),
    qTotS: inr(qSub + qGst, 0),
    qGstPctS: fmt(gstPct, 0),
  };
}
