import { useStore, useResults } from '../../state/store';
import { Button, Card, PrintIcon, cx } from '../../components/ui';
import logo from '../../assets/logo.svg';
import { orderOptions, resolveSel } from '../machines/model';
import { jobCardView } from './model';
import s from './JobCardScreen.module.css';

const printPage = () => setTimeout(() => window.print(), 100);

export function JobCardScreen() {
  const { state, actions } = useStore();
  const results = useResults();
  const opts = orderOptions(state.skus);
  const jobSelId = resolveSel(state.skus, state.jobSel);
  const sku = jobSelId ? state.skus.find((k) => k.id === jobSelId) : undefined;
  const r = jobSelId ? results[jobSelId] : undefined;
  const j = sku && r ? jobCardView(sku, r, new Date()) : null;

  return (
    <div data-screen-label="Job card" className={s.screen}>
      <div data-noprint="1" className={s.toolbar}>
        <select
          aria-label="Bag spec for job card"
          className={s.jobSelect}
          value={jobSelId || ''}
          onChange={(e) => actions.setState({ jobSel: e.target.value })}
        >
          {opts.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <div className={s.spacer} />
        <Button variant="primary" size="md" className={s.printBtn} icon={<PrintIcon size={15} />} onClick={printPage}>
          Print job card
        </Button>
      </div>

      {j && sku && (
        <Card id="jobcard" data-print-sheet="" className={s.sheet}>
          <div className={s.sheetHead}>
            <div>
              <img src={logo} alt="" className={s.logo} />
              <h2 className={s.sheetTitle}>Bag study report</h2>
            </div>
            <div className={s.sheetMeta}>
              <div className={s.mono}>{j.jobIdS}</div>
              <div>{j.dateS}</div>
            </div>
          </div>

          {/* 1. job summary (auto) */}
          <dl className={cx(s.dl, s.summary)}>
            <div>
              <dt>Job name</dt>
              <dd>{j.name}</dd>
            </div>
            <div>
              <dt>Party</dt>
              <dd>{j.party}</dd>
            </div>
            <div>
              <dt>Bag type</dt>
              <dd>{j.typeLabel}</dd>
            </div>
            <div>
              <dt>Qty</dt>
              <dd className={s.tabular}>
                {j.qtyS} pcs · {j.qtyKgS} kg
              </dd>
            </div>
          </dl>

          {/* 2. spec summary (auto) */}
          <section className={s.block}>
            <h3 className={s.blockTitle}>Bag spec (from calculator)</h3>
            <dl className={cx(s.dl, s.spec)}>
              {j.spec.map(([label, val]) => (
                <div key={label}>
                  <dt>{label}</dt> <dd>{val}</dd>
                </div>
              ))}
            </dl>
          </section>

          {/* editable sections */}
          {j.sections.map((sec) => (
            <section key={sec.title} className={s.block}>
              <h3 className={s.blockTitle}>{sec.title}</h3>
              <div className={s.fields}>
                {sec.fields.map((f) => (
                  <label key={f.key ?? f.label} className={s.field}>
                    {f.label}
                    {f.key == null ? (
                      <input className={s.fieldInput} value={f.val} readOnly />
                    ) : (
                      <input className={s.fieldInput} value={f.val} onChange={actions.bindJob(sku.id, f.key)} />
                    )}
                  </label>
                ))}
              </div>
            </section>
          ))}

          <div className={s.signatures}>
            <div>Prepared by ________________</div>
            <div>Production in-charge ________________</div>
            <div>Signature ________________</div>
          </div>
        </Card>
      )}
    </div>
  );
}
