import type { CalcResult, Sku, WastageAllowances } from '../../../engine';
import { quantitiesView } from './model';
import { InfoTile, TileStrip } from './Tile';
import r from './results.module.css';

/** Order quantities after wastage allowances (fabric, lamination, BOPP, bales). */
export function QuantitiesStrip({ sku, result, wast }: { sku: Sku; result: CalcResult; wast: WastageAllowances }) {
  const v = quantitiesView(sku, result, wast);
  return (
    <div>
      <h3 className={r.heading}>Order quantities (allowances applied, rounded up to 100 m)</h3>
      <TileStrip>
        <InfoTile label={'Fabric (' + v.fabAllowS + '%)'} value={v.fabMS + ' · ' + v.fabKgS} />
        {v.hasLam && <InfoTile label={'Lam (' + v.lamAllowS + '%)'} value={v.lamMS + ' · ' + v.lamKgS} />}
        {v.showBopp && <InfoTile label={'BOPP (' + v.boppAllowS + '%)'} value={v.boppMS + ' · ' + v.boppKgS} />}
        <InfoTile label="Bales" value={v.balesS} />
      </TileStrip>
    </div>
  );
}
