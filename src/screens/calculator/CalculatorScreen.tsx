import { APP_CONFIG } from '../../config';
import type { Layout } from '../../engine';
import { useResults, useStore } from '../../state/store';
import { ExpandIcon, PlusIcon, Segmented } from '../../components/ui';
import { SkuList } from './SkuList';
import { DetailCard } from './DetailCard';
import s from './CalculatorScreen.module.css';

const LAYOUT_OPTIONS: { value: Layout; label: string }[] = [
  { value: 'sheet', label: 'Sheet' },
  { value: 'split', label: 'Split' },
  { value: 'cards', label: 'Cards' },
];

/** After adding a spec, bring its detail card into view (as the design does). */
function scrollToDetail() {
  setTimeout(() => {
    const sc = document.querySelector<HTMLElement>('[data-scroll]');
    const card = sc && sc.querySelector<HTMLElement>('[data-detail-card]');
    if (sc && card) sc.scrollTo({ top: card.offsetTop - 70, behavior: 'smooth' });
  }, 120);
}

export function CalculatorScreen() {
  const { state, actions } = useStore();
  const results = useResults();
  const layout: Layout = state.layout ?? APP_CONFIG.defaultLayout;
  const expanded = !!state.tableExpand;
  const split = layout === 'split';
  const sel = state.skus.find((k) => k.id === state.selId);
  const selResult = sel ? results[sel.id] : undefined;

  return (
    <div data-screen-label="Calculator">
      <div data-noprint="1" className={s.toolbar}>
        <Segmented label="Layout" options={LAYOUT_OPTIONS} value={layout} onChange={(v) => actions.setState({ layout: v })} className={s.layoutSwitch} />
        <div className={s.count}>{state.skus.length} bag specs</div>
        <div className={s.spacer} />
        <button
          type="button"
          className={s.newBtn}
          onClick={() => {
            actions.addSku();
            scrollToDetail();
          }}
        >
          <PlusIcon size={14} />
          New bag spec
        </button>
        <button type="button" className={s.expandBtn} title="Show all bags in a full-page table" onClick={() => actions.setState({ tableExpand: !expanded })}>
          <ExpandIcon size={13} />
          {expanded ? 'Collapse view' : 'Expand full page'}
        </button>
      </div>

      <div className={split ? s.wrapSplit : s.wrapStack}>
        <div data-noprint="1" className={split ? s.listSplit : s.listStack}>
          <SkuList layout={layout} results={results} />
        </div>

        {sel && selResult && (
          <div data-detail-card="1" className={split ? s.detailSplit : s.detailStack}>
            <DetailCard sku={sel} result={selResult} />
          </div>
        )}
      </div>
    </div>
  );
}
