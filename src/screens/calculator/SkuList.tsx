import type { MouseEvent } from 'react';
import type { CalcResult, Layout, Sku, Unit } from '../../engine';
import { useStore } from '../../state/store';
import { Card, CardHeader, CellInput, CellSelect, CopyIcon, IconButton, TrashIcon, cx } from '../../components/ui';
import { TYPE_OPTIONS_SHORT, rowView } from './model';
import type { RowView } from './model';
import s from './SkuList.module.css';

interface ListProps {
  layout: Layout;
  results: Record<string, CalcResult>;
}

/** Bag specs, newest first, as a sheet table, a split list, or a card grid. */
export function SkuList({ layout, results }: ListProps) {
  const { state } = useStore();
  const rows = state.skus
    .slice()
    .reverse()
    .filter((k) => results[k.id])
    .map((k) => ({ sku: k, view: rowView(k, results[k.id], state.selId === k.id) }));

  if (layout === 'split') return <SplitList rows={rows} />;
  if (layout === 'cards') return <CardsGrid rows={rows} />;
  return <SheetTable rows={rows} total={state.skus.length} expanded={!!state.tableExpand} />;
}

type Rows = { sku: Sku; view: RowView }[];

const toneClass = (t: RowView['tone']) => (t === 'sel' ? s.sel : t === 'err' ? s.err : undefined);

// ---------------- Sheet ----------------

const COLUMNS: { label: string; cls: string }[] = [
  { label: 'Job', cls: s.thJob },
  { label: 'Type', cls: s.thType },
  { label: 'Unit', cls: s.thLeft },
  { label: 'W', cls: s.thRight },
  { label: 'L', cls: s.thRight },
  { label: 'Mesh w×f', cls: s.thRight },
  { label: 'Basis', cls: s.thLeft },
  { label: 'Wt g / Denier', cls: s.thRight },
  { label: 'GSM', cls: s.thRight },
  { label: 'Bag wt', cls: s.thRight },
  { label: 'Cost/bag', cls: s.thRight },
  { label: 'Quote/bag', cls: s.thRight },
  { label: 'Qty', cls: s.thRight },
];

function SheetTable({ rows, total, expanded }: { rows: Rows; total: number; expanded: boolean }) {
  return (
    <div className={s.sheetStack}>
      <Card clip>
        <CardHeader title={'All bags (' + total + ') — newest first'} />
        <div className={cx(s.sheetScroll, expanded && s.sheetScrollFull)}>
          <table className={s.table}>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.label} scope="col" className={cx(s.th, c.cls)}>
                    {c.label}
                  </th>
                ))}
                <th scope="col" className={cx(s.th, s.thActions)}>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ sku, view }) => (
                <SheetRow key={sku.id} sku={sku} view={view} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function SheetRow({ sku: k, view: v }: { sku: Sku; view: RowView }) {
  const { actions } = useStore();
  const lk = !!k.locked;
  const stop = (fn: () => void) => (e: MouseEvent) => {
    e.stopPropagation();
    fn();
  };
  return (
    <tr className={cx(s.tr, toneClass(v.tone))} onClick={() => actions.select(k.id)} title={v.errTitle}>
      <td className={s.tdName}>
        <CellInput aria-label="Bag name" value={k.name} onChange={actions.bindSku(k.id, 'name')} disabled={lk} className={s.nameCell} />
      </td>
      <td className={s.tdType}>
        <CellSelect aria-label="Bag type" value={k.type} onChange={(e) => actions.setSkuType(k.id, e.target.value)} disabled={lk} className={s.typeCell} options={TYPE_OPTIONS_SHORT} />
      </td>
      <td className={s.tdUnit}>
        <CellSelect aria-label="Unit" value={k.unit} onChange={(e) => actions.setUnit(k.id, e.target.value as Unit)} disabled={lk} className={s.unitCell}>
          <option value="in">in</option>
          <option value="cm">cm</option>
          <option value="mm">mm</option>
        </CellSelect>
      </td>
      <td className={s.tdNum}>
        <CellInput type="number" align="right" aria-label="Width" value={k.width} placeholder="W" onChange={actions.bindSku(k.id, 'width')} disabled={lk} className={s.dimCell} />
      </td>
      <td className={s.tdNum}>
        <CellInput type="number" align="right" aria-label="Length" value={k.length} placeholder="L" onChange={actions.bindSku(k.id, 'length')} disabled={lk} className={s.dimCell} />
      </td>
      <td className={cx(s.tdNum, s.nowrap)}>
        <CellInput type="number" align="right" aria-label="Warp mesh" value={k.warpMesh} placeholder="10" onChange={actions.bindSku(k.id, 'warpMesh')} disabled={lk} className={s.meshCell} />
        <span className={s.times}>×</span>
        <CellInput type="number" align="right" aria-label="Weft mesh" value={k.weftMesh} placeholder="10" onChange={actions.bindSku(k.id, 'weftMesh')} disabled={lk} className={s.meshCell} />
      </td>
      <td className={s.tdBasis}>
        <button
          type="button"
          className={s.modePill}
          title="Toggle: target weight → GSM, or denier → weight"
          onClick={stop(() => actions.updateSku(k.id, { mode: k.mode === 'wt' ? 'dn' : 'wt' }))}
        >
          {v.modeLabel}
        </button>
      </td>
      <td className={s.tdNum}>
        {k.mode === 'wt' ? (
          <input type="number" aria-label="Target wt g" value={k.targetWt} placeholder="g" onChange={actions.bindSku(k.id, 'targetWt')} disabled={lk} className={s.driverCell} />
        ) : (
          <input
            type="number"
            aria-label="Warp denier"
            value={k.warpD}
            placeholder="D"
            // weft follows warp while the two are still equal
            onChange={(e) => actions.updateSku(k.id, { warpD: e.target.value, weftD: k.warpD === k.weftD ? e.target.value : k.weftD })}
            disabled={lk}
            className={s.driverCell}
          />
        )}
      </td>
      <td className={s.tdStat}>{v.gsmS}</td>
      <td className={s.tdStat}>{v.wtS}</td>
      <td className={cx(s.tdStat, s.cost)}>{v.costS}</td>
      <td className={cx(s.tdStat, s.quote)}>{v.quoteS}</td>
      <td className={s.tdNum}>
        <CellInput type="number" align="right" aria-label="Qty" value={k.qty} placeholder="qty" onChange={actions.bindSku(k.id, 'qty')} disabled={lk} className={s.qtyCell} />
      </td>
      <td className={s.tdActions}>
        <IconButton label="Duplicate" onClick={stop(() => actions.dupSku(k.id))}>
          <CopyIcon size={14} />
        </IconButton>
        <IconButton label="Delete" tone="danger" onClick={stop(() => actions.delSku(k.id))}>
          <TrashIcon size={14} />
        </IconButton>
      </td>
    </tr>
  );
}

// ---------------- Split ----------------

function SplitList({ rows }: { rows: Rows }) {
  const { actions } = useStore();
  return (
    <Card clip>
      {rows.map(({ sku: k, view: v }) => (
        <button
          key={k.id}
          type="button"
          aria-current={v.tone === 'sel' ? 'true' : undefined}
          className={cx(s.splitRow, toneClass(v.tone))}
          onClick={() => actions.select(k.id)}
        >
          <span className={s.splitMain}>
            <span className={s.splitName}>{k.name}</span>
            <span className={s.splitSub}>
              {v.typeLabel} · {v.dimsS}
            </span>
          </span>
          <span className={s.splitSide}>
            <span className={s.splitQuote}>{v.quoteS}</span>
            <span className={cx(s.splitSub, s.tabular)}>
              {v.wtS} g · {v.gsmS} GSM
            </span>
          </span>
        </button>
      ))}
    </Card>
  );
}

// ---------------- Cards ----------------

function CardsGrid({ rows }: { rows: Rows }) {
  const { actions } = useStore();
  return (
    <div className={s.cardsGrid}>
      {rows.map(({ sku: k, view: v }) => (
        <button
          key={k.id}
          type="button"
          aria-current={v.tone === 'sel' ? 'true' : undefined}
          className={cx(s.bagCard, v.tone === 'sel' && s.bagCardSel, v.tone === 'err' && s.bagCardErr)}
          onClick={() => actions.select(k.id)}
        >
          <span className={s.cardHead}>
            <span className={s.cardName}>{k.name}</span>
            <span className={s.cardType}>{v.typeLabel}</span>
          </span>
          <span className={s.cardSub}>
            {v.dimsS} · mesh {String(k.warpMesh)}×{String(k.weftMesh)}
          </span>
          <span className={s.cardStats}>
            <CardStat label="GSM" value={v.gsmS} />
            <CardStat label="Bag wt" value={v.wtS + ' g'} />
            <CardStat label="Cost/bag" value={v.costS} />
            <CardStat label="Quote/bag" value={v.quoteS} strong />
          </span>
        </button>
      ))}
    </div>
  );
}

function CardStat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <span className={s.stat}>
      <span className={s.statLabel}>{label}</span>
      <span className={cx(s.statValue, strong && s.statStrong)}>{value}</span>
    </span>
  );
}
