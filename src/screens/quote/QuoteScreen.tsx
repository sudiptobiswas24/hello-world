import { useId } from 'react';
import { APP_CONFIG } from '../../config';
import { useResults, useStore } from '../../state/store';
import { Button, Card, Field, PrintIcon, TextInput, cx } from '../../components/ui';
import logo from '../../assets/logo.svg';
import { quoteView } from './model';
import s from './QuoteScreen.module.css';

const printPage = () => setTimeout(() => window.print(), 100);

export function QuoteScreen() {
  const { state, actions } = useStore();
  const results = useResults();
  const linesId = useId();
  const q = quoteView(state, results, new Date());
  const companyName = APP_CONFIG.companyName || 'Bellwort Papers Pvt Ltd';

  const setQuote = (patch: { customer?: string; gstin?: string }) => actions.setState((st) => ({ quote: Object.assign({}, st.quote, patch) }));
  const toggleLine = (id: string) =>
    actions.setState((st) => {
      const sel = st.quote.sel || {};
      return { quote: Object.assign({}, st.quote, { sel: Object.assign({}, sel, { [id]: !sel[id] }) }) };
    });

  return (
    <div data-screen-label="Quote" className={s.screen}>
      <Card data-noprint="1" className={s.panel}>
        <h2 className={s.panelHead}>Quote details</h2>
        <div className={s.panelBody}>
          <Field label="Customer">
            {(id) => (
              <TextInput id={id} value={state.quote.customer ?? ''} onChange={(e) => setQuote({ customer: e.target.value })} placeholder="Customer name" />
            )}
          </Field>
          <Field label="GSTIN">
            {(id) => (
              <TextInput id={id} mono value={state.quote.gstin ?? ''} onChange={(e) => setQuote({ gstin: e.target.value })} placeholder="22AAAAA0000A1Z5" />
            )}
          </Field>
          <div role="group" aria-labelledby={linesId}>
            <div id={linesId} className={s.groupLabel}>
              Line items
            </div>
            {q.qLines.map((l) => (
              <label key={l.id} className={s.line}>
                <input type="checkbox" checked={l.checked} onChange={() => toggleLine(l.id)} />
                <span className={s.lineName}>{l.name}</span>
                <span className={s.lineRate}>
                  {l.qtyS} × {l.rateS}
                </span>
              </label>
            ))}
          </div>
          <Button variant="primary" size="md" className={s.printBtn} icon={<PrintIcon size={15} />} onClick={printPage}>
            Print quote
          </Button>
        </div>
      </Card>

      <Card id="qsheet" data-print-sheet="" className={s.sheet}>
        <div className={s.sheetHead}>
          <div>
            <img src={logo} alt="" className={s.logo} />
            <div className={s.company}>{companyName}</div>
            <div className={s.tagline}>Woven sacks · costing &amp; quotation</div>
          </div>
          <div className={s.headRight}>
            <h2 className={s.sheetTitle}>Quotation</h2>
            <div className={s.qNo}>{q.qNo}</div>
            <div className={s.qDate}>{q.qDate}</div>
          </div>
        </div>

        <div className={s.meta}>
          <div>
            <div className={s.metaLabel}>To</div>
            <div className={s.metaValue}>{q.qCustomerShow}</div>
            <div className={s.metaSub}>{q.qGstinShow}</div>
          </div>
          <div>
            <div className={s.metaLabel}>Validity</div>
            <div className={s.metaValue}>15 days</div>
          </div>
          <div>
            <div className={s.metaLabel}>HSN</div>
            <div className={cx(s.metaValue, s.hsn)}>6305 33 00</div>
          </div>
        </div>

        <table className={s.table}>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Description</th>
              <th scope="col" className={s.num}>
                Qty
              </th>
              <th scope="col" className={s.num}>
                Rate / bag
              </th>
              <th scope="col" className={s.num}>
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {q.quoteRows.map((r) => (
              <tr key={r.id}>
                <td className={s.idx}>{r.i}</td>
                <td>
                  <div className={s.rowName}>{r.name}</div>
                  <div className={s.rowDesc}>{r.desc}</div>
                </td>
                <td className={s.num}>{r.qtyS}</td>
                <td className={s.num}>{r.rateS}</td>
                <td className={cx(s.num, s.amt)}>{r.amtS}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className={s.totalsWrap}>
          <div className={s.totals}>
            <div className={s.totalRow}>
              <span>Subtotal</span>
              <span>{q.qSubS}</span>
            </div>
            <div className={cx(s.totalRow, s.totalRowRule)}>
              <span>GST @ {q.qGstPctS}%</span>
              <span>{q.qGstS}</span>
            </div>
            <div className={s.grandRow}>
              <span>Total</span>
              <span>{q.qTotS}</span>
            </div>
          </div>
        </div>

        <div className={s.terms}>
          Rates ex-works, subject to PP granule market. Payment: 50% advance, balance against dispatch. Tolerance ±2% on bag weight and count.
        </div>
      </Card>
    </div>
  );
}
