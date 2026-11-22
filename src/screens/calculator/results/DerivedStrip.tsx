import type { CalcResult, Sku } from '../../../engine';
import { derivedView } from './model';
import { StatTile, TileStrip } from './Tile';
import r from './results.module.css';

/** Derived figures (denier, GSM, meter weights, cut length, weights) and the calculation-chain note. */
export function DerivedStrip({ sku, result }: { sku: Sku; result: CalcResult }) {
  const v = derivedView(sku, result);
  return (
    <>
      <div className={r.derived}>
        <TileStrip>
          <StatTile brand alert={v.denWarn ? 'warn' : null} label="Machine denier" value={v.denS} />
          <StatTile brand label="Unlam denier" value={v.mdenS} title="Machine denier − 4% shrink" />
          {v.showLamDen && <StatTile brand label="Lam denier" value={v.lamDenS} title="Denier computed from laminated meter weight" />}
        </TileStrip>
        <TileStrip>
          <StatTile brand alert={v.gsmBad ? 'danger' : null} label="Unlam fabric GSM" value={v.gsmS} />
          <StatTile label="Unlam GPM" value={v.gpmUnlamS} title="Fabric only, unlaminated" />
          {v.showLamDen && <StatTile label="Lam GPM" value={v.gpmLamS} title="Fabric + lamination coating" />}
          <StatTile label="Fabric gram" value={v.fabGramS} title="GPM ÷ width in inches — trade quality number" />
          <StatTile label="Total meter weight" value={v.gpmS} title="Total meter weight incl. films" />
        </TileStrip>
        <TileStrip>
          <StatTile label="Cut length" value={v.cutS} />
          <StatTile label="Net fabric wt" value={v.netS + ' g'} title="Without yarn, handle, liner" />
          <StatTile alert={v.wtBad ? 'danger' : null} label="Bag weight" value={v.wtS + ' g'} />
        </TileStrip>
      </div>
      <div className={r.chainNote}>{v.chainNote}</div>
    </>
  );
}
